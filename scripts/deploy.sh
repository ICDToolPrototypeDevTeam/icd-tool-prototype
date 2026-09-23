#!/usr/bin/env bash
# =============================================================================
# ICD 工具原型 · 服务器部署脚本
#
# 在**交付包目录内**执行：bash deploy.sh
# 依次做四件事：校验归档 → 导入镜像 → 复核架构与目录 → 启动并健康自检。
#
# 幂等：重复执行只会重新 load 镜像并按新镜像重建容器，
#       **不会**动 backend/output/ 里的历史分析结果。
#
# 刻意不做的事（避免脚本替人做决定）：
#   - 不覆盖已存在的 backend/.env，只在缺失时从 .env.example 复制一份并提醒填密钥；
#   - 不删除任何输出文件、容器与镜像。
#
# 选项：
#   --skip-verify   跳过 sha256 校验（只有在无法校验时才用）
#   --no-start      只导入镜像与准备目录，不启动容器
# =============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" # compose 里的相对路径按本目录解析，必须先切进来

COMPOSE_FILE="docker-compose.server.yml"
BUILD_INFO="BUILD_INFO"
SKIP_VERIFY=0
NO_START=0

for arg in "$@"; do
  case "$arg" in
    --skip-verify) SKIP_VERIFY=1 ;;
    --no-start) NO_START=1 ;;
    -h | --help)
      sed -n '2,20p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      printf '[错误] 未知参数：%s（可用：--skip-verify / --no-start）\n' "$arg" >&2
      exit 1
      ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\n[注意] %s\n' "$*" >&2; }
die() {
  printf '\n[错误] %s\n' "$*" >&2
  exit 1
}

http_get() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- --timeout=5 "$1"
  else
    return 127
  fi
}

http_status() {
  command -v curl >/dev/null 2>&1 || return 127
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1"
}

# ------------------------------------------------------------------ 前置检查
command -v docker >/dev/null 2>&1 || die "找不到 docker 命令（本机未安装 Docker？）"
docker info >/dev/null 2>&1 || die "docker 守护进程没有响应（docker 服务启动了吗？）"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "找不到 docker compose 插件，也没有 docker-compose 命令"
fi
info "使用编排命令：${COMPOSE[*]}"

[ -f "$COMPOSE_FILE" ] || die "当前目录缺少 $COMPOSE_FILE（本脚本必须放在交付包目录内执行）"
[ -f "$BUILD_INFO" ] || warn "缺少 $BUILD_INFO，将跳过架构复核"

IMAGE="$(sed -n 's/^image=//p' "$BUILD_INFO" 2>/dev/null | head -1 || true)"
ARCH="$(sed -n 's/^arch=//p' "$BUILD_INFO" 2>/dev/null | head -1 || true)"
ARCHIVE="$(sed -n 's/^archive=//p' "$BUILD_INFO" 2>/dev/null | head -1 || true)"
SHA="$(sed -n 's/^sha256=//p' "$BUILD_INFO" 2>/dev/null | head -1 || true)"
ARCHIVE="${ARCHIVE:-icd-tool-backend.tar.gz}"

[ -f "$ARCHIVE" ] || die "找不到镜像归档 $ARCHIVE"
[ -n "$IMAGE" ] || die "$BUILD_INFO 里没有 image= 行，无法确定镜像标签"
info "镜像标签：$IMAGE"
info "归档文件：$ARCHIVE"

# ------------------------------------------------------------------ 1. 校验
say "[1/4] 校验归档完整性"
if [ "$SKIP_VERIFY" = "1" ]; then
  warn "已按 --skip-verify 跳过 sha256 校验"
else
  [ -n "$SHA" ] || die "$BUILD_INFO 里没有 sha256= 行，无法校验（确认无误后可加 --skip-verify）"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$SHA" "$ARCHIVE" | sha256sum -c - || die "归档校验失败：文件损坏或传输不完整，请重新拷贝"
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$SHA" "$ARCHIVE" | shasum -a 256 -c - || die "归档校验失败：文件损坏或传输不完整，请重新拷贝"
  elif command -v openssl >/dev/null 2>&1; then
    GET_SHA="$(openssl dgst -sha256 "$ARCHIVE" | awk '{print $NF}' || true)"
    [ -n "$GET_SHA" ] || die "openssl 计算 sha256 失败"
    [ "$GET_SHA" = "$SHA" ] || die "归档校验失败：期望 $SHA，实际 $GET_SHA"
  else
    warn "找不到 sha256sum / shasum / openssl，跳过校验（如归档有疑问请重新拷贝）"
  fi
  info "sha256 与 $BUILD_INFO 记录一致"
fi

# ------------------------------------------------------------------ 2. 架构
say "[2/4] 复核 CPU 架构"
case "$(uname -m)" in
  x86_64 | amd64) HOST_ARCH="linux/amd64" ;;
  aarch64 | arm64) HOST_ARCH="linux/arm64" ;;
  *) HOST_ARCH="linux/$(uname -m)" ;;
esac
info "本机：$(uname -m) → $HOST_ARCH"
if [ -z "$ARCH" ]; then
  warn "BUILD_INFO 里没有 arch= 行，跳过架构复核"
elif [ "$ARCH" != "$HOST_ARCH" ]; then
  die "镜像架构（$ARCH）与本机（$HOST_ARCH）不一致，容器会以 exec format error 启动失败。
      请使用与本机架构一致的交付包，或让对方重新构建 $HOST_ARCH 的镜像。"
else
  info "与镜像架构一致（$ARCH）"
fi

# ------------------------------------------------------------------ 3. 导入
say "[3/4] 导入镜像（大镜像需要一两分钟）"
docker load -i "$ARCHIVE" || {
  warn "docker load 直接读取失败，改用 gunzip 后重试"
  gunzip -c "$ARCHIVE" | docker load || die "镜像导入失败，请把上面的报错回传"
}
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "镜像 $IMAGE 没有导入成功"
info "已导入：$(docker image inspect --format '{{.Id}}（{{.Os}}/{{.Architecture}}）' "$IMAGE")"

# 目录与配置：compose 以相对路径挂载这些位置，缺了会起不来或页面 404
mkdir -p backend/output frontend/dist
[ -f frontend/dist/index.html ] || warn "frontend/dist/index.html 不存在，页面会 404；请确认交付包完整"
ENV_CREATED=0
if [ ! -f backend/.env ]; then
  if [ -f backend/.env.example ]; then
    cp backend/.env.example backend/.env
    ENV_CREATED=1
  fi
fi

if [ "$NO_START" = "1" ]; then
  say "[4/4] 已按 --no-start 停在启动之前"
  info "启动命令：${COMPOSE[*]} -f $COMPOSE_FILE up -d"
  exit 0
fi

# ------------------------------------------------------------------ 4. 启动
say "[4/4] 启动服务并自检"
UP_LOG="$(mktemp)"
trap 'rm -f "$UP_LOG"' EXIT

if ! "${COMPOSE[@]}" -f "$COMPOSE_FILE" up -d >"$UP_LOG" 2>&1; then
  cat "$UP_LOG" >&2
  if grep -q 'pull_policy' "$UP_LOG"; then
    warn "本机 Compose 版本不认 pull_policy，改用去掉该行的临时编排文件重试"
    COMPOSE_FILE="docker-compose.server.effective.yml"
    sed '/pull_policy:/d' docker-compose.server.yml >"$COMPOSE_FILE"
    "${COMPOSE[@]}" -f "$COMPOSE_FILE" up -d || die "启动失败，请把上面的报错回传"
    info "已生成 $COMPOSE_FILE（内容仅少了 pull_policy 一行），后续命令请沿用这个文件名"
  else
    die "启动失败，请把上面的报错回传"
  fi
else
  cat "$UP_LOG"
fi

# 端口取编排文件里的映射（默认 8000），保持一致
PORT="$(sed -n "s/^[[:space:]]*-[[:space:]]*'\{0,1\}\([0-9]\{1,5\}\):8000'\{0,1\}[[:space:]]*$/\1/p" "$COMPOSE_FILE" | head -1 || true)"
PORT="${PORT:-8000}"

if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
  warn "本机没有 curl/wget，跳过自动自检。请手动确认：docker ps、浏览器打开 http://<服务器IP>:${PORT}"
else
  info "等待服务就绪（最多 60 秒）"
  READY=0
  for _ in $(seq 1 30); do
    if http_get "http://127.0.0.1:${PORT}/api/v4/health" 2>/dev/null | grep -q '"status"'; then
      READY=1
      break
    fi
    sleep 2
  done

  if [ "$READY" = "1" ]; then
    info "健康检查：$(http_get "http://127.0.0.1:${PORT}/api/v4/health")"
    CODE="$(http_status "http://127.0.0.1:${PORT}/" 2>/dev/null || true)"
    case "$CODE" in
      200) info "前端首页：HTTP 200" ;;
      127 | "") warn "本机没有 curl，跳过首页状态检查（健康接口已用 wget 确认过）" ;;
      *) warn "前端首页返回 HTTP $CODE（期望 200），检查 frontend/dist 是否完整" ;;
    esac
  else
    warn "60 秒内没有等到健康响应，最近日志如下："
    "${COMPOSE[@]}" -f "$COMPOSE_FILE" logs --tail 50 backend || true
    die "服务未就绪，请把上面的日志回传"
  fi
fi

# 访问地址里的 IP：hostname -I 在部分环境（busybox、精简发行版、Git Bash）不支持。
# set -e + set -o pipefail 下，命令替换失败会直接终止脚本——部署明明成功了却看不到汇总。
IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -n "$IP" ] || IP="$(hostname -f 2>/dev/null || true)"
[ -n "$IP" ] || IP="$(hostname 2>/dev/null || true)"

cat <<EOF

部署完成
  访问地址：http://${IP:-<服务器IP>}:${PORT}
  容器状态：${COMPOSE[*]} -f $COMPOSE_FILE ps
  实时日志：${COMPOSE[*]} -f $COMPOSE_FILE logs -f backend
  输出目录：backend/output/v4/<任务ID>/
EOF

if [ "$ENV_CREATED" = "1" ]; then
  warn "backend/.env 是刚从 .env.example 复制的模板，**模型地址与密钥还是空的**。
      填好之后必须重建容器才会生效（restart 不会重新读 .env）：
        ${COMPOSE[*]} -f $COMPOSE_FILE up -d --force-recreate backend"
fi

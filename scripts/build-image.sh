#!/usr/bin/env bash
# =============================================================================
# ICD 工具原型 · 服务器交付包构建脚本
#
# **构建方在本机执行**，服务器运维用不到它（服务器只用随包给出的 deploy.sh）。
# 本机（Windows 的 Git Bash）与 Linux 均可运行，需要 docker 与 node/npm。
#
# 为什么不能只给一句 `docker compose build`：
#   服务器形态是「一个后端容器同时提供接口与页面」（ICD_STATIC_DIR=/app/static），
#   前端**不打成镜像**，而是把 `npm run build` 生成的 frontend/dist 挂进后端容器。
#   所以一次可交付的构建 = 后端镜像归档 + 前端构建产物 + 编排文件 + 部署脚本，
#   四样缺一不可。上一轮就踩过「重建了前端镜像，但服务器用的 dist 没更新」。
#
# 用法（在仓库根目录执行）：
#   bash scripts/build-image.sh
#   VERSION=4.1 bash scripts/build-image.sh           # 换镜像标签（需同步 compose 的 image）
#   SKIP_FRONTEND=1 bash scripts/build-image.sh       # 前端产物没动过，只重建后端镜像
#   PLATFORM=linux/amd64 bash scripts/build-image.sh  # 显式指定目标架构
#   NO_TAR=1 bash scripts/build-image.sh              # 不额外打单文件包（只留目录）
#
# 产物：
#   dist/icd-deploy-<日期>/          可直接拷到服务器的交付目录
#   dist/icd-deploy-<日期>.tar       同一份内容的单文件形式（未压缩，内含镜像已压缩）
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-4.0}"
IMAGE="icd-tool-backend-v${VERSION}"
STAMP="${STAMP:-$(date +%Y%m%d)}"
BUNDLE="$ROOT/dist/icd-deploy-${STAMP}"
ARCHIVE_NAME="icd-tool-backend.tar.gz"

say() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die() {
  printf '\n[错误] %s\n' "$*" >&2
  exit 1
}

# 压缩流：有 pigz 就用（大镜像快很多），否则退回 gzip
compress_stream() {
  if command -v pigz >/dev/null 2>&1; then pigz -9; else gzip -9; fi
}

cd "$ROOT"

command -v docker >/dev/null 2>&1 || die "找不到 docker 命令"
docker info >/dev/null 2>&1 || die "docker 守护进程没有响应（Docker Desktop 启动了吗？）"

# ---------------------------------------------------------------- 1. 前端产物
if [ "${SKIP_FRONTEND:-0}" = "1" ]; then
  say "[1/6] 跳过前端构建（SKIP_FRONTEND=1）"
  [ -f "$ROOT/frontend/dist/index.html" ] || die "frontend/dist 不存在或为空，不能跳过前端构建"
else
  say "[1/6] 构建前端产物（npm run build → frontend/dist）"
  command -v npm >/dev/null 2>&1 || die "找不到 npm 命令（需要 Node.js 环境）"
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    info "首次构建：先安装前端依赖"
    (cd "$ROOT/frontend" && npm install)
  fi
  (cd "$ROOT/frontend" && npm run build)
  [ -f "$ROOT/frontend/dist/index.html" ] || die "前端构建结束但没有产出 frontend/dist/index.html"
  info "前端产物：$(find "$ROOT/frontend/dist" -type f | wc -l | tr -d ' ') 个文件，$(du -sh "$ROOT/frontend/dist" | cut -f1)"
fi

# ---------------------------------------------------------------- 2. 后端镜像
# 目标架构取本机 docker 的架构（交付镜像必须与服务器架构一致，deploy.sh 会复核）。
PLATFORM="${PLATFORM:-linux/$(docker version --format '{{.Server.Arch}}')}"
say "[2/6] 构建后端镜像 $IMAGE（$PLATFORM）"
info "构建上下文 backend/：.dockerignore 已排除 output/ 与 .env"
docker build --platform "$PLATFORM" -t "$IMAGE" "$ROOT/backend"

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
IMAGE_ARCH="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$IMAGE")"
info "镜像 $IMAGE_ID（$IMAGE_ARCH）"

# ---------------------------------------------------------------- 3. 导出归档
say "[3/6] 导出镜像归档 $ARCHIVE_NAME（大镜像压缩需要一两分钟）"
mkdir -p "$ROOT/dist"
if [ -e "$BUNDLE" ]; then
  # 只覆盖本脚本生成的包：没有 BUILD_INFO 说明是别人放的东西，不擅自删
  [ -f "$BUNDLE/BUILD_INFO" ] || die "$BUNDLE 已存在且不是本脚本生成的（缺 BUILD_INFO），请手动处理后重试"
  info "覆盖上一次的同名交付包：$BUNDLE"
  rm -rf "$BUNDLE"
fi
mkdir -p "$BUNDLE"
docker save "$IMAGE" | compress_stream > "$BUNDLE/$ARCHIVE_NAME"
ARCHIVE_SIZE="$(du -h "$BUNDLE/$ARCHIVE_NAME" | cut -f1)"
info "归档大小：$ARCHIVE_SIZE"

# ---------------------------------------------------------------- 4. 组装交付包
say "[4/6] 组装交付包"
cp "$ROOT/docker-compose.server.yml" "$BUNDLE/"
cp "$ROOT/部署说明.md" "$BUNDLE/"
mkdir -p "$BUNDLE/frontend" "$BUNDLE/backend/output"
cp -r "$ROOT/frontend/dist" "$BUNDLE/frontend/dist"
# .env.example 随包给出（真 .env 含密钥，另行走单独渠道，绝不放进交付包）
cp "$ROOT/backend/.env.example" "$BUNDLE/backend/.env.example"
# deploy.sh 强转 LF：Windows 工作区里可能是 CRLF，带到 Linux 上会报 \r 之类的怪错
sed 's/\r$//' "$ROOT/scripts/deploy.sh" > "$BUNDLE/deploy.sh"
chmod +x "$BUNDLE/deploy.sh"
info "frontend/dist、backend/.env.example、docker-compose.server.yml、部署说明.md、deploy.sh 已就位"

# ---------------------------------------------------------------- 5. 校验值
say "[5/6] 写入 BUILD_INFO 与校验值"
(
  cd "$BUNDLE"
  sha256sum "$ARCHIVE_NAME" > "$ARCHIVE_NAME.sha256"
)
ARCHIVE_SHA="$(cut -d' ' -f1 < "$BUNDLE/$ARCHIVE_NAME.sha256")"
GIT_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if ! git -C "$ROOT" diff --quiet HEAD 2>/dev/null; then
  GIT_SHA="$GIT_SHA+（构建时工作区有未提交改动）"
fi
cat > "$BUNDLE/BUILD_INFO" <<EOF
ICD 工具原型 · 服务器交付包
构建时间：$(date '+%Y-%m-%d %H:%M:%S %z')
源码版本：$GIT_SHA
前端产物：frontend/dist（由 npm run build 生成）
镜像标签：$IMAGE
镜像 ID：$IMAGE_ID
镜像架构：$IMAGE_ARCH
归档文件：$ARCHIVE_NAME（$ARCHIVE_SIZE）
归档 sha256：$ARCHIVE_SHA

--- 供 deploy.sh 读取，请勿改动以下行 ---
image=$IMAGE
arch=$IMAGE_ARCH
archive=$ARCHIVE_NAME
sha256=$ARCHIVE_SHA
EOF
info "sha256：$ARCHIVE_SHA"

# ---------------------------------------------------------------- 6. 收尾
say "[6/6] 完成"
info "交付目录：$BUNDLE（$(du -sh "$BUNDLE" | cut -f1)）"
if [ "${NO_TAR:-0}" != "1" ]; then
  TARBALL="$ROOT/dist/icd-deploy-${STAMP}.tar"
  info "打单文件包：$TARBALL（未压缩，内含镜像本身已是 .tar.gz）"
  rm -f "$TARBALL"
  (cd "$ROOT/dist" && tar -cf "icd-deploy-${STAMP}.tar" "icd-deploy-${STAMP}")
  info "单文件大小：$(du -h "$TARBALL" | cut -f1)"
fi

cat <<EOF

下一步：
  1) 把交付目录（或单文件包）拷到服务器；
  2) 服务器上进入该目录执行：bash deploy.sh
     （校验 sha256 → docker load → 检查架构与目录 → 启动 → 健康自检）
  3) 首次部署前确认 backend/.env 里的模型地址与密钥已填好。

详细说明见交付包内的 部署说明.md。
EOF

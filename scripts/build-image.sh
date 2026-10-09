#!/usr/bin/env bash
# =============================================================================
# ICD 工具原型 · 服务器交付包构建脚本
#
# **构建方在本机执行**，服务器运维用不到它（服务器按交付包内 部署说明.md
# 逐条手工部署，不执行任何随包脚本）。
# 本机（Windows 的 Git Bash）与 Linux 均可运行，需要 docker 与 node/npm。
#
# 为什么不能只给一句 `docker compose build`：
#   服务器形态是「一个后端容器同时提供接口与页面」（ICD_STATIC_DIR=/app/static），
#   前端**不打成镜像**，而是把 `npm run build` 生成的 frontend/dist 挂进后端容器。
#   所以一次可交付的构建 = 后端镜像归档 + 前端构建产物 + 编排文件 + 部署说明
#   （与 更新说明.md），缺一不可。上一轮就踩过「重建了前端镜像，但服务器用的
#   dist 没更新」。
#
# 出包前置：先更新仓库根的 更新说明.md（本版相比上一版的部署层变化、需 root 的
# 步骤、回滚方式——构建时会核对它的日期并提醒）。构建还会在包内版本末尾自动
# 追加「与上一版交付包的机械对比」附录（文件级差异，不代替人工判断）。
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

# ===== 更新说明.md 附录用（emit_prev_diff 及其辅助函数，可单独抽取做回归）=====

# 文件对比 → 一句话结论（未变化 / 已变化（字节数） / 上一版无此文件）
compare_file() {
  local prev="$1" cur="$2"
  if [ ! -f "$prev" ]; then
    printf '上一版无此文件'
  elif cmp -s "$prev" "$cur"; then
    printf '未变化'
  else
    printf '已变化（%s → %s 字节）' "$(wc -c < "$prev" | tr -d ' ')" "$(wc -c < "$cur" | tr -d ' ')"
  fi
}

# 目录内容树哈希（只看文件内容，与文件系统顺序无关）
tree_hash() {
  ( cd "$1" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1 )
}

# 与上一版交付包的部署层对比：追加到本包 更新说明.md 末尾（构建时自动生成）。
# 单独成函数，便于不跑完整构建也能抽取它做回归。
emit_prev_diff() {
  local prev="" d
  if [ -d "$ROOT/dist" ]; then
    for d in "$ROOT/dist"/icd-deploy-*/; do
      d="${d%/}"
      if [ "$d" != "$BUNDLE" ] && [ -f "$d/BUILD_INFO" ]; then
        prev="$d"
      fi
    done
  fi
  local out="$BUNDLE/更新说明.md"
  local prev_time prev_src prev_sha cur_sha sha_line fh
  {
    printf '\n---\n\n'
    printf '## 附：与上一版交付包的机械对比（构建时自动生成，请勿手改本节）\n\n'
    if [ -z "$prev" ]; then
      printf '本包是 dist/ 下第一个交付包，无对比基准。\n'
    else
      prev_time="$(sed -n 's/^构建时间：//p' "$prev/BUILD_INFO" | tr -d '\r')"
      prev_src="$(sed -n 's/^源码版本：//p' "$prev/BUILD_INFO" | tr -d '\r')"
      printf '对比基准：`%s`（构建时间 %s，源码版本 %s）\n\n' \
        "$(basename "$prev")" "${prev_time:-未知}" "${prev_src:-未知}"
      prev_sha="$(sed -n 's/^sha256=//p' "$prev/BUILD_INFO" | tr -d '\r')"
      cur_sha="$(sed -n 's/^sha256=//p' "$BUNDLE/BUILD_INFO" | tr -d '\r')"
      if [ -n "$prev_sha" ] && [ "$prev_sha" = "$cur_sha" ]; then
        sha_line='未变化'
      elif [ -n "$prev_sha" ] && [ -n "$cur_sha" ]; then
        sha_line="已变化（${prev_sha:0:12}… → ${cur_sha:0:12}…）"
      else
        sha_line='已变化（一侧 BUILD_INFO 缺 sha256= 行）'
      fi
      if [ -d "$prev/frontend/dist" ] && [ -d "$BUNDLE/frontend/dist" ]; then
        if [ "$(tree_hash "$prev/frontend/dist")" = "$(tree_hash "$BUNDLE/frontend/dist")" ]; then
          fh='未变化'
        else
          fh='已变化（内容哈希不同）'
        fi
      else
        fh='无法比对（一侧缺目录）'
      fi
      printf '| 项目 | 结果 |\n| --- | --- |\n'
      printf '| 镜像归档 sha256 | %s |\n' "$sha_line"
      printf '| docker-compose.server.yml | %s |\n' "$(compare_file "$prev/docker-compose.server.yml" "$BUNDLE/docker-compose.server.yml")"
      printf '| backend/.env.example | %s |\n' "$(compare_file "$prev/backend/.env.example" "$BUNDLE/backend/.env.example")"
      printf '| 部署说明.md | %s |\n' "$(compare_file "$prev/部署说明.md" "$BUNDLE/部署说明.md")"
      printf '| frontend/dist | %s |\n' "$fh"
      printf '| 更新说明.md | %s |\n' "$(compare_file "$prev/更新说明.md" "$BUNDLE/更新说明.md")"
      printf '\n> 本表只说明「文件是否变化」，部署影响以正文第 1 节人工判断为准。\n'
    fi
  } >> "$out"
}

cd "$ROOT"

command -v docker >/dev/null 2>&1 || die "找不到 docker 命令"
docker info >/dev/null 2>&1 || die "docker 守护进程没有响应（Docker Desktop 启动了吗？）"

# ---------------------------------------------------------------- 1. 前端产物
if [ "${SKIP_FRONTEND:-0}" = "1" ]; then
  say "[1/7] 跳过前端构建（SKIP_FRONTEND=1）"
  [ -f "$ROOT/frontend/dist/index.html" ] || die "frontend/dist 不存在或为空，不能跳过前端构建"
else
  say "[1/7] 构建前端产物（npm run build → frontend/dist）"
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
# 目标架构取本机 docker 的架构（交付镜像必须与服务器架构一致，
# 部署时按 部署说明.md §5.1 用 uname -m 与 BUILD_INFO 的 arch= 复核）。
PLATFORM="${PLATFORM:-linux/$(docker version --format '{{.Server.Arch}}')}"
say "[2/7] 构建后端镜像 $IMAGE（$PLATFORM）"
info "构建上下文 backend/：.dockerignore 已排除 output/ 与 .env"
docker build --platform "$PLATFORM" -t "$IMAGE" "$ROOT/backend"

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
IMAGE_ARCH="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$IMAGE")"
info "镜像 $IMAGE_ID（$IMAGE_ARCH）"

# ---------------------------------------------------------------- 3. 导出归档
say "[3/7] 导出镜像归档 $ARCHIVE_NAME（大镜像压缩需要一两分钟）"
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
say "[4/7] 组装交付包"
cp "$ROOT/docker-compose.server.yml" "$BUNDLE/"
cp "$ROOT/部署说明.md" "$BUNDLE/"
# 更新说明.md：面向运维的「本版 vs 上一版」部署变化说明；发布前必须人工更新到本版
cp "$ROOT/更新说明.md" "$BUNDLE/"
mkdir -p "$BUNDLE/frontend" "$BUNDLE/backend/output"
cp -r "$ROOT/frontend/dist" "$BUNDLE/frontend/dist"
# .env.example 随包给出（真 .env 含密钥，另行走单独渠道，绝不放进交付包）
cp "$ROOT/backend/.env.example" "$BUNDLE/backend/.env.example"
info "frontend/dist、backend/.env.example、docker-compose.server.yml、部署说明.md、更新说明.md 已就位"

# 更新说明.md 日期核对：顶部日期（YYYY-MM-DD）应为出包当天，不符则提醒（不阻断构建）
NOTE_DATE="$(sed -n '1s/.*\([0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}\).*/\1/p' "$ROOT/更新说明.md" | tr -d '\r')"
TODAY="$(date +%Y-%m-%d)"
if [ "$NOTE_DATE" != "$TODAY" ]; then
  printf '\n[提醒] 更新说明.md 顶部日期为「%s」，不是今天（%s）。\n' "${NOTE_DATE:-未标注日期}" "$TODAY"
  printf '       如本版有部署层变化，请先更新该文件再分发（当前内容将原样打进交付包）。\n'
fi

# ---------------------------------------------------------------- 5. 校验值
say "[5/7] 写入 BUILD_INFO 与校验值"
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

--- 机器可读区（部署核对用：arch= 供 uname -m 对照，sha256= 供手工校验），请勿改动以下行 ---
image=$IMAGE
arch=$IMAGE_ARCH
archive=$ARCHIVE_NAME
sha256=$ARCHIVE_SHA
EOF
info "sha256：$ARCHIVE_SHA"

# ---------------------------------------------------------------- 6. 更新说明附录
say "[6/7] 生成与上一版交付包的机械对比（追加到本包 更新说明.md）"
emit_prev_diff
info "已写入 更新说明.md 附录（对比基准与逐项结果见文件）"

# ---------------------------------------------------------------- 7. 收尾
say "[7/7] 完成"
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
  2) 服务器上先读包内 更新说明.md（本版部署变化与需 root 的步骤），
     再按 部署说明.md §5 逐条手工执行（不执行任何随包脚本）：
     sha256 校验 → docker load → 目录与 .env → 内存上限 → 启动自检；
  3) 首次部署前确认 backend/.env 里的模型地址与密钥已填好。

详细说明见交付包内的 部署说明.md。
EOF

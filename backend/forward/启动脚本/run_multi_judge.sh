#!/usr/bin/env bash
# ============================================================
# EoICD -> HLR 正向检查：多 judge 协同（嫁接）一键启动脚本（Git Bash 版）
# 用法:  bash run_multi_judge.sh [config]      config 默认 项目根/multi_judge_config.json
#
# 作用:
#   1. cd 到项目根目录（本脚本位于 启动脚本/ 子目录，上一级即项目根）
#   2. 选择 Python 解释器（eoicd_integration venv，含依赖）
#   3. 从本脚本同目录的 .env.multijudge 读取各 judge / arbitrator 的 API Key，
#      以及可选的 AI_MODEL / BASE_URL（单 judge 风格：config 中 judge 省略 model/base_url
#      或写 ${AI_MODEL}/${BASE_URL} 时，从本 .env 读取）。
#   4. 调用 multi_judge_runner.py（共享阶段1 + 派生各 judge 子进程 + 聚合 + 复核 + 落盘）
# ============================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# 1. Python 解释器
PYTHON="${PYTHON:-C:/Users/zhaokl/.workbuddy/binaries/python/envs/eoicd_integration/Scripts/python.exe}"
[ -x "$PYTHON" ] || PYTHON="python"
WINPY="$PYTHON"
if command -v cygpath >/dev/null 2>&1 && [[ "$PYTHON" == *"/"* ]]; then
  WINPY="$(cygpath -w "$PYTHON" 2>/dev/null || echo "$PYTHON")"
fi

# 2. Config
CONFIG="${1:-multi_judge_config.json}"
[ -f "$CONFIG" ] || { echo "[错误] 未找到配置文件: $CONFIG"; exit 1; }

# 3. 读取各 judge / arbitrator API Key（变量名需与 config 里 api_key_env 对应）
ENV_FILE="$SCRIPT_DIR/.env.multijudge"
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"                      # 去 # 注释
    line="$(echo "$line" | tr -d '\r')"
    [ -z "$line" ] && continue
    case "$line" in
      *=*) export "${line%%=*}"="${line#*=}" ;;
    esac
  done < "$ENV_FILE"
else
  echo "[错误] 未找到 $ENV_FILE"
  echo "  请复制 .env.multijudge.example 为 .env.multijudge 并填入真实 Key："
  echo "    cp 启动脚本/.env.multijudge.example 启动脚本/.env.multijudge"
  exit 1
fi

echo "Config : $CONFIG"
"$WINPY" multi_judge_runner.py --config "$CONFIG"

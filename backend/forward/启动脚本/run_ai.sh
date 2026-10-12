#!/usr/bin/env bash
# ============================================================
# EoICD -> HLR 正向检查：完整 AI 匹配启动脚本（Git Bash 版）
# 用法:  bash run_ai.sh [project]        project=ams(默认)|eps
#
# 作用:
#   1. cd 到项目根目录（本脚本位于 启动脚本/ 子目录，上一级即项目根，
#      保证 run_integration.py 的相对路径/模块路径正确）
#   2. 选择 Python 解释器（带依赖的 venv，否则 PATH 上的 python）
#   3. 从本脚本同目录的 .env 读取 DEEPSEEK_API_KEY（已被 gitignore 忽略）
#   4. 自动探测 input/<project>/ 下的 EoICD 与 HLR 文件
#   5. 运行 run_integration.py（不带 --skip-ai => AI 匹配开启）
# ============================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 切到项目根目录（本脚本在 启动脚本/ 下，上一级即项目根）
cd "$SCRIPT_DIR/.."

# 1. Python 解释器（需装 openpyxl/python-docx/requests）
# 优先级：环境变量 PYTHON > 项目内 .venv > PATH 的 python
#   → 拿到工程后只需建 .venv 并装依赖（见 requirements.txt），无需修改本脚本路径
if [ -n "$PYTHON" ]; then
  :
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi
[ -x "$PYTHON" ] || PYTHON="python"
# 转成 Windows 原生反斜杠路径，避免子进程 CreateProcess 因 / 路径报 WinError 2
WINPY="$PYTHON"
if command -v cygpath >/dev/null 2>&1 && [[ "$PYTHON" == *"/"* ]]; then
  WINPY="$(cygpath -w "$PYTHON" 2>/dev/null || echo "$PYTHON")"
fi

# 2. 项目
PROJECT="${1:-ams}"

# 3. 读取 DEEPSEEK_API_KEY（优先本脚本同目录的 .env，其次已有环境变量）
if [ -f "$SCRIPT_DIR/.env" ]; then
  ENV_KEY="$(grep -E '^[[:space:]]*DEEPSEEK_API_KEY[[:space:]]*=' "$SCRIPT_DIR/.env" | head -1 | tr -d '\r' | cut -d= -f2-)"
  ENV_KEY="${ENV_KEY#"${ENV_KEY%%[![:space:]]*}"}"   # 去前导空格
  ENV_KEY="${ENV_KEY%"${ENV_KEY##*[![:space:]]}"}"   # 去尾部空格
  [ -n "$ENV_KEY" ] && export DEEPSEEK_API_KEY="$ENV_KEY"
fi
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "[错误] 未找到 DEEPSEEK_API_KEY。"
  echo "  请在该脚本同目录（启动脚本/.env）创建 .env 并写入: DEEPSEEK_API_KEY=sk-xxxx"
  echo "  或在运行前导出环境变量:  export DEEPSEEK_API_KEY=sk-xxxx"
  exit 1
fi

# 4. 自动探测 input/<PROJECT>/ 下的 EoICD 与 HLR（项目根目录）
EOICD_PUB="$(ls input/$PROJECT/*EoICD*Publisher*.xlsx 2>/dev/null | head -1)"
EOICD_SUB="$(ls input/$PROJECT/*EoICD*Subscriber*.xlsx 2>/dev/null | head -1)"
HLR="$(ls input/$PROJECT/*.docx 2>/dev/null | head -1)"

[ -n "$EOICD_PUB" ] || { echo "[错误] 未找到 EoICD Publisher xlsx (input/$PROJECT/)"; exit 1; }
[ -n "$EOICD_SUB" ] || { echo "[错误] 未找到 EoICD Subscriber xlsx (input/$PROJECT/)"; exit 1; }
[ -n "$HLR" ]       || { echo "[错误] 未找到 HLR .docx (input/$PROJECT/)"; exit 1; }

# 5. 运行（注意：不带 --skip-ai，AI 匹配自动启用）
echo "Project : $PROJECT"
echo "EoICD Pub: $EOICD_PUB"
echo "EoICD Sub: $EOICD_SUB"
echo "HLR      : $HLR"
"$WINPY" run_integration.py --eoicd-pub "$EOICD_PUB" --eoicd-sub "$EOICD_SUB" --hlr "$HLR" --project "$PROJECT"

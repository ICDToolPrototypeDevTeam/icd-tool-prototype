@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM EoICD 到 HLR forward check : 多 judge 协同（嫁接）一键启动器
REM Usage : double-click, or  run_multi_judge.bat [config]
REM         config = 多 judge 配置文件（默认 项目根/multi_judge_config.json）
REM
REM What it does:
REM   1. cd 到项目根目录（本脚本位于 启动脚本\ 子目录，上一级即项目根）
REM   2. 选择 Python 解释器（eoicd_integration venv，含 openpyxl/python-docx/requests）
REM   3. 从本脚本同目录的 .env.multijudge 读取各 judge / arbitrator 的 API Key，
REM      以及可选的 AI_MODEL / BASE_URL（单 judge 风格：config 中 judge 省略 model/base_url
REM      或写 ${AI_MODEL}/${BASE_URL} 时，从本 .env 读取）。
REM      （变量名需与 multi_judge_config.json 里的 api_key_env 对应；
REM        通用加载：忽略 # 注释行与空行，把每一行 KEY=VALUE 注入当前环境）
REM   4. 调用 multi_judge_runner.py —— 它负责共享阶段1、按 config 派生各 judge 子进程、
REM      聚合、AI 复核闭环，并最终落盘带时间戳的 Word / Excel 到 output/
REM ============================================================
setlocal
REM 强制所有 Python 子进程以 UTF-8 输出，否则 Windows 下 stdout 为管道时回退 GBK 崩溃
set "PYTHONIOENCODING=utf-8"
set "SCRIPT_DIR=%~dp0"
REM 切到项目根目录（本脚本位于 启动脚本\ 子目录，上一级即项目根）
cd /d "%SCRIPT_DIR%.."

REM ---- 1. Python interpreter (需含 openpyxl/python-docx/requests) ----
set "PYTHON=C:\Users\zhaokl\.workbuddy\binaries\python\envs\eoicd_integration\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

REM ---- 2. Config（第一个参数，默认 项目根/multi_judge_config.json）----
set "CONFIG=%~1"
if "%CONFIG%"=="" set "CONFIG=multi_judge_config.json"
if not exist "%CONFIG%" (
  echo [ERROR] 未找到配置文件: %CONFIG%
  echo   请将配置文件放到项目根目录，或在调用时指定: run_multi_judge.bat 路径\你的配置.json
  pause & exit /b 1
)

REM ---- 3. Load 各 judge / arbitrator API Key from .env.multijudge NEXT TO THIS SCRIPT ----
REM      runner 会子进程内把 config 的 model/base_url 注入；若 config 中 judge 省略 model/base_url
REM      或写成 ${AI_MODEL}/${BASE_URL}，则改从本 .env.multijudge 的 AI_MODEL/BASE_URL 读取（单 judge 风格）。
REM      本脚本通用加载：任何 KEY=VALUE（含可选的 AI_MODEL/BASE_URL）都会进环境。
set "ENV_FILE=%SCRIPT_DIR%.env.multijudge"
if not exist "%ENV_FILE%" (
  echo [ERROR] 未找到 %ENV_FILE%
  echo   请复制 .env.multijudge.example 为 .env.multijudge 并填入真实 Key：
  echo     copy 启动脚本\.env.multijudge.example 启动脚本\.env.multijudge
  pause & exit /b 1
)
REM 通用加载：跳过以 # 开头的注释行，把每行 KEY=VALUE 注入当前环境
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do set "%%A=%%~B"

echo Config : %CONFIG%
echo.
"%PYTHON%" multi_judge_runner.py --config "%CONFIG%"
pause

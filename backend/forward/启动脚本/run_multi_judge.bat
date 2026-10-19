@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM EoICD -> HLR forward check : multi-judge (grafted) one-click launcher
REM Usage : double-click, or  run_multi_judge.bat [config]
REM         config = multi-judge config file (default: project_root/multi_judge_config.json)
REM
REM What it does:
REM   1. cd to project root (this script lives in the launcher subfolder; parent is project root)
REM   2. Pick Python interpreter (.venv with openpyxl/python-docx/requests)
REM   3. Load each judge/arbitrator API Key from .env.multijudge next to this script,
REM      plus optional AI_MODEL / BASE_URL (single-judge style).
REM      (var names must match api_key_env in multi_judge_config.json;
REM       generic loader: ignore # comment lines and blank lines, inject KEY=VALUE)
REM   4. Call multi_judge_runner.py -- it runs shared stage1, spawns per-judge subprocesses
REM      per config, aggregates, closes the AI review loop, and writes timestamped
REM      Word/Excel to output/
REM ============================================================
setlocal
REM Force all Python subprocess stdout to UTF-8 (Windows falls back to GBK on piped stdout -> crash)
set "PYTHONIOENCODING=utf-8"
set "SCRIPT_DIR=%~dp0"
REM cd to project root (this script is in the launcher subfolder; parent is project root)
cd /d "%SCRIPT_DIR%.."

REM ---- 1. Python interpreter (needs openpyxl/python-docx/requests) ----
REM priority: env PYTHON > project .venv\Scripts\python.exe > PATH python
if defined PYTHON (
    set "PYTHON=%PYTHON%"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)
if not exist "%PYTHON%" set "PYTHON=python"

REM ---- 2. Config (first arg, default project_root/multi_judge_config.json) ----
set "CONFIG=%~1"
if "%CONFIG%"=="" set "CONFIG=multi_judge_config.json"
if not exist "%CONFIG%" (
  echo [ERROR] config not found: %CONFIG%
  echo    put the config at project root, or pass it: run_multi_judge.bat path\to\your_config.json
  pause & exit /b 1
)

REM ---- 3. Load judge/arbitrator API Keys from .env.multijudge next to this script ----
REM     runner injects model/base_url inside each judge subprocess; if a judge omits
REM     model/base_url or writes ${AI_MODEL}/${BASE_URL}, it reads AI_MODEL/BASE_URL from here.
REM     Generic loader: any KEY=VALUE (incl. optional AI_MODEL/BASE_URL) goes to env.
set "ENV_FILE=%~sdp0.env.multijudge"
if not exist "%ENV_FILE%" set "ENV_FILE=%SCRIPT_DIR%.env.multijudge"
if not exist "%ENV_FILE%" (
  echo [ERROR] %ENV_FILE% not found
  echo    copy .env.multijudge.example to .env.multijudge and fill real keys:
  echo     (run: copy .env.multijudge.example .env.multijudge  in this folder)
  pause & exit /b 1
)
REM Generic loader: skip lines starting with #, inject each KEY=VALUE into env
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do set "%%A=%%~B"

echo Config : %CONFIG%
echo.
echo PYTHON解释器路径："%PYTHON%"
echo Python可执行文件是否存在？
if exist "%PYTHON%" (echo ✅ python.exe 存在) else (echo ❌ python.exe 找不到！)
echo 当前工作目录：%cd%
echo 配置文件路径：%CONFIG%
echo ================== Python 开始执行 ==================
"%PYTHON%" multi_judge_runner.py --config "%CONFIG%" 2>&1
echo ================== Python 执行结束，返回码=%errorlevel% ==================
pause
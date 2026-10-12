@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM EoICD 到 HLR forward check : full AI matching launcher
REM Usage : double-click, or  run_ai.bat [project]
REM         project = hscu
REM
REM What it does:
REM   1. cd to PROJECT ROOT (this script's own folder is "启动脚本/",
REM      so we go one level up) -- keeps run_integration.py paths valid
REM   2. pick the Python interpreter (venv with deps, else PATH python)
REM   3. read AI connection env from a local .env file NEXT TO THIS SCRIPT
REM      (gitignored; do NOT commit real key)
REM      Required keys: API_KEY / BASE_URL / AI_MODEL（已去除历史兼容别名）.
REM   4. auto-detect EoICD xlsx + HLR docx under input/<project>/
REM   5. run run_integration.py WITHOUT --skip-ai  即 AI matching ON
REM   6. 追溯过滤现已自动判断（无需 --use-trace）:
REM      input/<project>/ 中若存在追溯表(设备需求与系统ICD追溯表.xlsx)则自动启用
REM      追溯过滤版结果；不存在则自动回退全量。不再需要手动传 --use-trace。
REM ============================================================
setlocal
REM 强制所有 Python 子进程(含被 run_integration.py 用 PIPE 捕获的子脚本)以 UTF-8 输出,
REM 否则 Windows 下 stdout 为管道时会回退到 GBK 编码, 无法编码 emoji 而崩溃。
set "PYTHONIOENCODING=utf-8"
set "SCRIPT_DIR=%~dp0"
REM 切到项目根目录（本脚本位于 启动脚本\ 子目录，上一级即项目根）
cd /d "%SCRIPT_DIR%.."

REM ---- 1. Python interpreter（需含 openpyxl/python-docx/requests）----
REM 优先级：环境变量 PYTHON > 项目内 .venv\Scripts\python.exe > PATH 的 python
REM   → 拿到工程后只需建 .venv 并装依赖（见 requirements.txt），无需修改本脚本路径
if defined PYTHON (
    set "PYTHON=%PYTHON%"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)
if not exist "%PYTHON%" set "PYTHON=python"

REM ---- 2. Project (first CLI arg, default ams) ----
set "PROJECT=%~1"
if "%PROJECT%"=="" set "PROJECT=hscu"

REM ---- 3. Load AI env (API_KEY + BASE_URL + AI_MODEL) from .env next to THIS script ----
REM      仅读取 API_KEY / BASE_URL / AI_MODEL 三个规范变量名；注释行(以 # 开头)
REM      不会命中白名单，故被自然忽略。 ----
set "API_KEY="
set "BASE_URL="
set "AI_MODEL="
if exist "%SCRIPT_DIR%.env" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%SCRIPT_DIR%.env") do (
    if /i "%%~A"=="API_KEY"   set "API_KEY=%%~B"
    if /i "%%~A"=="BASE_URL"  set "BASE_URL=%%~B"
    if /i "%%~A"=="AI_MODEL"  set "AI_MODEL=%%~B"
  )
)
if not defined API_KEY (
  echo [ERROR] API_KEY not found.
  echo   Create a .env file next to run_ai.bat 启动脚本\.env with: API_KEY=sk-xxxx
  echo   Or set it in the environment before running:   set API_KEY=sk-xxxx
  pause
  exit /b 1
)

REM ---- 4. Auto-detect EoICD + HLR inputs under input/项目/ (project root) ----
for %%F in (input\%PROJECT%\*EoICD*Publisher*.xlsx) do set "EOICD_PUB=%%F"
for %%F in (input\%PROJECT%\*EoICD*Subscriber*.xlsx) do set "EOICD_SUB=%%F"
for %%F in (input\%PROJECT%\*.docx) do set "HLR=%%F"

REM 两侧都缺失才是真正错误；仅缺一侧属于 publisher-only / subscriber-only，允许
if not defined EOICD_PUB (
  if not defined EOICD_SUB (
    echo [ERROR] Neither EoICD Publisher nor Subscriber xlsx found under input\%PROJECT%\
    pause & exit /b 1
  ) else (
    echo [WARN] No EoICD Publisher xlsx found (subscriber-only project is OK)
  )
)
if not defined EOICD_SUB (
  echo [WARN] No EoICD Subscriber xlsx found under input\%PROJECT% (publisher-only project is OK)
)
if not defined HLR (
  echo [ERROR] Cannot find HLR .docx under input\%PROJECT%\
  pause & exit /b 1
)

REM ---- 5. Run (NOTE: no --skip-ai 即 AI matching is enabled) ----
echo Project : %PROJECT%
echo EoICD Pub : %EOICD_PUB%
echo EoICD Sub : %EOICD_SUB%
echo HLR       : %HLR%
echo.
REM 仅当对应侧文件存在时才传对应参数（publisher-only / subscriber-only 均支持）
set "PUB_ARG="
if defined EOICD_PUB set "PUB_ARG=--eoicd-pub "%EOICD_PUB%""
set "SUB_ARG="
if defined EOICD_SUB set "SUB_ARG=--eoicd-sub "%EOICD_SUB%""
"%PYTHON%" run_integration.py %PUB_ARG% %SUB_ARG% --hlr "%HLR%" --project %PROJECT%
pause

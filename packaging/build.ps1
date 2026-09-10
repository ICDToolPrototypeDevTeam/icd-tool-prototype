# One-click build script: frontend build + PyInstaller + assemble output.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Requires Python 3.11 and Node.js on PATH.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot   # repo root

Write-Host "==> [1/5] Frontend build (npm run build)" -ForegroundColor Cyan
Push-Location "$Root\frontend"
npm run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
Pop-Location

Write-Host "==> [2/5] Prepare build venv and install deps" -ForegroundColor Cyan
$Venv = "$Root\.venv-build"
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    python -m venv $Venv
}
& "$Venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Venv\Scripts\python.exe" -m pip install -r "$Root\backend\requirements.txt" pyinstaller

Write-Host "==> [3/5] PyInstaller build" -ForegroundColor Cyan
Push-Location $Root
& "$Venv\Scripts\pyinstaller.exe" --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build" "$Root\packaging\ICDTool.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
Pop-Location

Write-Host "==> [4/5] Copy frontend dist to output static/" -ForegroundColor Cyan
$Static = "$Root\dist\ICDTool\static"
if (Test-Path $Static) { Remove-Item -Recurse -Force $Static }
Copy-Item -Recurse "$Root\frontend\dist" $Static

Write-Host "==> [5/5] Create .env from backend/.env.example" -ForegroundColor Cyan
$EnvTarget = "$Root\dist\ICDTool\.env"
if (-not (Test-Path $EnvTarget)) {
    Copy-Item "$Root\backend\.env.example" $EnvTarget
    Write-Host "Created $EnvTarget (fill API keys or keep USE_MOCK_LLM=1 for mock mode)" -ForegroundColor Yellow
} else {
    Write-Host "$EnvTarget already exists, skipped (avoid overwriting user keys)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done! Output dir: $Root\dist\ICDTool\" -ForegroundColor Green
Write-Host "Double-click ICDTool.exe to start; browser opens http://127.0.0.1:8000"

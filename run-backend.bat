@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0omniflow-backend"
if exist "%~dp0.venv\Scripts\activate.bat" (
  call "%~dp0.venv\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else (
  echo [error] .venv not found in repo root or omniflow-backend & exit /b 1
)
uvicorn src.gateway.main:create_app --factory --host 0.0.0.0 --port 8000

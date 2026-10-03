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
pip show win32-setctime >nul 2>&1 || pip install win32-setctime
python -m src.ai_workers.llm_invoker.worker

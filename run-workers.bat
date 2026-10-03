@echo off
rem Opens each worker in its own console window.
start "omniflow-router" cmd /k "%~dp0run-worker-router.bat"
start "omniflow-llm" cmd /k "%~dp0run-worker-llm.bat"
start "omniflow-outbound" cmd /k "%~dp0run-worker-outbound.bat"

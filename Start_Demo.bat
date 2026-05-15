@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo Rural Bus Transit Intelligence - Local Demo
echo ===================================================

if not exist ".env" (
  echo .env file not found. Copy .env.example to .env and add your API keys first.
  pause
  exit /b 1
)

echo Checking Python dependencies...
python -m pip install -r requirements.txt

echo.
echo Starting Master Orchestrator (Backend + Agent)...
echo Please wait for the server to spin up, then open your browser.
echo UI:  http://localhost:5000
echo API: http://localhost:8000
echo.
python purulia_pipeline_orchestrator.py
pause

@echo off
echo ===================================================
echo Purulia Transit OS - Gemma Hackathon Local Demo
echo ===================================================

echo Checking Python dependencies...
pip install -r requirements.txt

echo.
echo Starting Master Orchestrator (Backend + Agent)...
echo Please wait for the server to spin up, then open your browser.
echo.
python purulia_pipeline_orchestrator.py
pause

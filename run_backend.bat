@echo off
echo Starting Data-Cosmos Backend...
cd /d "%~dp0backend"
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
pause

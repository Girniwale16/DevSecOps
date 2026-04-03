@echo off
echo Starting Data-Cosmos Frontend (chatbot.py)...
cd /d "%~dp0frontend"
uv run main.py
pause

@echo off
cd /d "%~dp0"
echo Starting Futures Spread Research...
echo.
echo Web page: http://127.0.0.1:8000
echo Keep this window open while the bot is running.
echo Press Ctrl+C here to stop the server.
echo.
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn web_app:app --host 127.0.0.1 --port 8000
pause

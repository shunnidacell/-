Set-Location -Path $PSScriptRoot
Write-Host "Starting Futures Spread Research..."
Write-Host "Web page: http://127.0.0.1:8000"
Write-Host "Keep this window open while the bot is running."
Start-Process "http://127.0.0.1:8000"
& ".\.venv\Scripts\python.exe" -m uvicorn web_app:app --host 127.0.0.1 --port 8000

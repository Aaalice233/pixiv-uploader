@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Virtual environment not found: %PYTHON%
    pause
    exit /b 1
)
echo Starting Pixiv Uploader...
"%PYTHON%" web_server.py
pause

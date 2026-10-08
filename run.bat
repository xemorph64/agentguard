@echo off
setlocal
title AgentGuard
rem One-command start for Windows: creates .venv on first run, then serves API + console on :8000
cd /d "%~dp0"
if "%PORT%"=="" set "PORT=8000"
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo [AgentGuard] First run: creating a virtual environment in .venv ...
    where py >nul 2>nul && py -3 -m venv "%VENV%"
    if not exist "%PY%" python -m venv "%VENV%"
)
if not exist "%PY%" (
    echo [ERROR] Python 3.11 or newer was not found.
    echo         Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

rem install once; the marker means a half-finished install is retried next time
if not exist "%VENV%\.agentguard-installed" (
    echo [AgentGuard] Installing dependencies - takes a few minutes the first time ...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r backend\requirements.txt
    if errorlevel 1 (
        echo [ERROR] Installing dependencies failed. Check your internet connection and run this again.
        pause
        exit /b 1
    )
    echo ok> "%VENV%\.agentguard-installed"
)

echo.
echo [AgentGuard] Starting on http://localhost:%PORT%   (close this window or press Ctrl+C to stop)
echo.
rem open the browser a few seconds after the server starts
start "" /min cmd /c "timeout /t 5 /nobreak >nul && start http://localhost:%PORT%"
cd backend
"%PY%" -m uvicorn main:app --host 127.0.0.1 --port %PORT%
pause

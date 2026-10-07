@echo off
rem One-click start on Windows: creates the venv on first run, then serves API + dashboard on :8000
cd /d "%~dp0"
if not exist .venv (
  where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
  if errorlevel 1 (
    echo Python not found. Install Python 3.12+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    pause
    exit /b 1
  )
  .venv\Scripts\python -m pip install -r backend\requirements.txt
)
if "%PORT%"=="" set PORT=8000
cd backend
echo.
echo   AgentGuard running at http://localhost:%PORT%   (Ctrl+C to stop)
echo.
start "" http://localhost:%PORT%
..\.venv\Scripts\python -m uvicorn main:app --port %PORT%
pause

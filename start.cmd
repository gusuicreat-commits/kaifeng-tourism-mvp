@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if not errorlevel 1 (
    py -3 scripts/start.py %*
) else (
    python scripts/start.py %*
)
if errorlevel 1 (
    echo Startup failed. Check the message above. Python 3.11+ and Node.js 22.12+ are required.
    pause
    exit /b 1
)

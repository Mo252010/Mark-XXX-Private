@echo off
setlocal
title A.T.L.A.S. - INTELLIGENCE SYSTEM
mode con: cols=85 lines=25
color 0b

echo.
echo   =====================================================================
echo      ____  ____  __      __    ____     ______  __  __  ____  ____ 
echo     / __ \/ __ \/ /     /  \  / __ \   / __  / / / / / / __ \/ __ \
echo    / /_/ / /_/ / /     / /\ \/ /_/ /  / /_/ / / / / / / /_/ / /_/ /
echo   / __  / __  / /___  / /__\  __  /  / __  / / /_/ / / __  / __  / 
echo  /_/ /_/_/ /_/_____/ /_/    \_\/_/  /_/ /_/ /_____/ /_/ /_/_/ /_/  
echo   =====================================================================
echo.
echo  [SYSTEM] Loading A.T.L.A.S. Core...
echo.

cd /d "%~dp0"

:: Activate virtual environment if it exists
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

if not exist "config\api_keys.json" (
    echo.
    echo  [ERROR] Missing config\api_keys.json
    echo  [HINT] Run: python setup.py
    pause
    exit /b 1
)

:RESTART
:: Run ATLAS
python main.py

:: Restart logic
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] A.T.L.A.S. crashed or disconnected (Error: %errorlevel%).
    echo  [RECOVERY] Restarting core in 5 seconds...
    timeout /t 5 >nul
    goto RESTART
)

echo.
echo  [SYSTEM] ATLAS shut down normally.
pause

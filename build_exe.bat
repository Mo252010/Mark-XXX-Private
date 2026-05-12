@echo off
title ATLAS PyInstaller Build Script
echo Setting up environment for compiling A.T.L.A.S...

cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo Installing pyinstaller...
pip install pyinstaller

echo Building A.T.L.A.S...
pyinstaller --noconfirm --onedir --windowed --name "ATLAS" --add-data "core;core" --add-data "actions;actions" --add-data "agent;agent" --add-data "memory;memory" --add-data "config;config" --add-data "face_v1.png;." --hidden-import "google.genai" --hidden-import "google.generativeai" "main.py"

echo.
echo Build completed! You can find the compiled exe in the 'dist\ATLAS' folder.
pause

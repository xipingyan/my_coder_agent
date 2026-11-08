@echo off
rem Build the Windows NSIS installer for the Coder Agent GUI.
rem Requirements: Node 20+ and a venv at backend\.venv with PyInstaller
rem   backend\.venv\Scripts\pip install -e . pyinstaller
setlocal
cd /d "%~dp0"

echo ==^> building backend standalone executable
backend\.venv\Scripts\python backend\build_standalone.py gui\build-resources\backend
if errorlevel 1 exit /b 1

echo ==^> building GUI (electron-builder)
cd gui
call npm run dist:win
if errorlevel 1 exit /b 1

echo ==^> done, artifacts in gui\release\
dir release

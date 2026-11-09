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

echo ==^> collecting server artifact
cd ..
for /f "delims=" %%v in ('node -p "require('./gui/package.json').version"') do set VER=%%v
rem unpacked runnable binary (release\code_agent-<ver>-win)
if exist "gui\release\code_agent-%VER%-win" rmdir /s /q "gui\release\code_agent-%VER%-win"
move "gui\release\win-unpacked" "gui\release\code_agent-%VER%-win" >nul
copy gui\build-resources\backend\coder-agent-server.exe "gui\release\coder-agent-server-%VER%.win.exe"
(
echo @echo off
echo rem Coder Agent server launcher (Windows).
echo rem Usage: coder-agent-server-%VER%.win.bat [port] [token]
echo rem then in the GUI: Settings ^> Connection ^> mode "remote", host, port, token.
echo setlocal
echo cd /d "%%~dp0"
echo set PORT=%1
echo if "%PORT%"=="" set PORT=8931
echo set TOKEN=%2
echo if "%TOKEN%"=="" (
echo   "coder-agent-server-%VER%.win.exe" --host 0.0.0.0 --port %PORT%
echo ) else (
echo   "coder-agent-server-%VER%.win.exe" --host 0.0.0.0 --port %PORT% --token %TOKEN%
echo )
) > "gui\release\coder-agent-server-%VER%.win.bat"

echo ==^> done, artifacts in gui\release\
dir gui\release

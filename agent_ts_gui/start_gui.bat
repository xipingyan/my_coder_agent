@echo off
rem Launch the Coder Agent GUI (Electron front-end + Python backend) on Windows.
rem Usage: start_gui.bat [--root C:\path\to\project]
setlocal
cd /d "%~dp0"

rem Node 20+ must be on PATH (check: node -v)
cd gui
call npm run build
if errorlevel 1 exit /b 1

rem --no-sandbox: not needed on Windows (no root sandbox); --disable-gpu keeps
rem the CDP smoke tests stable on machines without a GPU.
node_modules\.bin\electron.cmd --disable-gpu . %*

@echo off
rem Launch the Coder Agent GUI (Electron front-end + Python backend) on Windows.
rem Usage: start_gui.bat [--root C:\path\to\project]
setlocal
cd /d "%~dp0" || exit /b 1

rem Node 20+ must be on PATH (check: node -v)
cd gui || (echo [start_gui] gui\ not found next to this script & exit /b 1)
call npm run build
if errorlevel 1 (
  echo [start_gui] build failed - fix the errors above
  exit /b 1
)

rem --no-sandbox: not needed on Windows (no root sandbox); --disable-gpu keeps
rem the CDP smoke tests stable on machines without a GPU.
if not exist "node_modules\.bin\electron.cmd" goto :electron_missing
if not exist "dist\main\index.js" goto :build_missing
rem Pass the app as an ABSOLUTE path: electron's "Unable to find Electron app
rem at ..." dialog appears when the relative "." does not resolve to this dir.
node_modules\.bin\electron.cmd --disable-gpu "%CD%" %*
exit /b

:electron_missing
echo [start_gui] electron not found: run "npm install" in gui\ first
exit /b 1

:build_missing
echo [start_gui] build output missing, dist\main\index.js - check the build output above
exit /b 1

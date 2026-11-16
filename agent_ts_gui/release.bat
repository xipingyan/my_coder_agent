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
rem unpacked runnable binary (release\coder-agent-<ver>-win)
if exist "release\coder-agent-%VER%-win" rmdir /s /q "release\coder-agent-%VER%-win"
move "release\win-unpacked" "release\coder-agent-%VER%-win" >nul
copy gui\build-resources\backend\coder-agent-server.exe "release\coder-agent-server-%VER%.win.exe"
rem server launcher (no version in name): background start, idempotent, supports "stop"
(
echo @echo off
echo rem Coder Agent server launcher (Windows, background).
echo rem Usage:
echo rem   coder-agent-server.bat [start] [port] [token]   start in background (default)
echo rem   coder-agent-server.bat stop                     stop the running server
echo rem then in the GUI: Settings ^> Connection ^> mode "remote", host, port, token.
echo setlocal
echo cd /d "%%~dp0"
echo set BIN=coder-agent-server-%VER%.win.exe
echo set PIDFILE=coder-agent-server.pid
echo if "%%1"=="" goto start
echo if "%%1"=="stop" goto stop
echo if "%%1"=="start" goto start
echo usage: coder-agent-server.bat [start [port] [token] ^| stop]
echo exit /b 1
echo :stop
echo if exist "%%PIDFILE%%" (
echo   for /f "usebackq delims=" %%p in ("%%PIDFILE%%") do set PID=%%p
echo   tasklist /FI "PID eq %%PID%%" 2^>nul ^| findstr /I "%%BIN%%" ^>nul
echo   if not errorlevel 1 (
echo     taskkill /F /PID %%PID%% ^>nul
echo     del "%%PIDFILE%%"
echo     echo coder-agent-server stopped
echo     exit /b 0
echo   )
echo )
echo del "%%PIDFILE%%" 2^>nul
echo coder-agent-server not running
echo exit /b 0
echo :start
echo if exist "%%PIDFILE%%" (
echo   for /f "usebackq delims=" %%p in ("%%PIDFILE%%") do set PID=%%p
echo   tasklist /FI "PID eq %%PID%%" 2^>nul ^| findstr /I "%%BIN%%" ^>nul
echo   if not errorlevel 1 (
echo     echo coder-agent-server already running (pid %%PID%%)
echo     exit /b 0
echo   )
echo )
echo set PORT=%%2
echo if "%%PORT%%"=="" set PORT=8931
echo set TOKEN=%%3
echo if "%%TOKEN%%"=="" (
echo   set ARGS=--host 0.0.0.0 --port %%PORT%%
echo ) else (
echo   set ARGS=--host 0.0.0.0 --port %%PORT%% --token %%TOKEN%%
echo )
echo for /f "tokens=2 delims=:" %%p in ('wmic process call create "%%~dp0%%BIN%% %%ARGS%%" ^| findstr "ProcessId "') do (
echo   echo %%p^>%%PIDFILE%%
echo   echo coder-agent-server started (pid %%p^)
echo )
) > "release\coder-agent-server.bat"
rem plain-name launcher so customers can just double-click "coder-agent"
(
echo @echo off
echo start "" "%%~dp0coder-agent-%VER%-win\coder-agent.exe"
) > "release\coder-agent.bat"
rem systemd unit for the Linux server machine (GUI on Windows + server on Linux)
copy /y coder-agent-server.service "release\coder-agent-server.service" >nul
rem usage guide for the end user
copy /y guide_release.txt "release\guide.txt" >nul

echo ==^> done, artifacts in release\
dir release

@echo off
rem Regression suite for agent_ts_gui on Windows: backend pytest + GUI CDP smoke tests.
rem Usage:
rem   run_tests.bat            everything
rem   run_tests.bat backend    backend pytest only
rem   run_tests.bat gui        all GUI CDP tests
rem   run_tests.bat remote     remote test (needs a standalone server on 127.0.0.1:8931)
rem   run_tests.bat <name>     one GUI test by name (boot, theme, split, proj, ...)
setlocal
cd /d "%~dp0"

set TARGET=%1
if "%TARGET%"=="" set TARGET=all
set FAILED=

if "%TARGET%"=="all" call :run_backend
if "%TARGET%"=="backend" goto summary

if "%TARGET%"=="gui" goto run_gui_all
if "%TARGET%"=="remote" (
  echo (remote test needs a standalone server on 127.0.0.1:8931 - start it first)
  call :run_gui remote
  goto summary
)
if "%TARGET%"=="all" goto run_gui_all

rem single GUI test by name
call :run_gui %TARGET%
goto summary

:run_backend
echo === backend pytest ===
call backend\.venv\Scripts\python -m pytest backend\tests\ -q
if errorlevel 1 set FAILED=%FAILED% backend
exit /b 0

:run_gui_all
for %%t in (boot theme split proj proj_switch ctxmenu files local_add localpick restart_close debug) do call :run_gui %%t
exit /b 0

:run_gui
echo === gui: %1 ===
pushd gui
call node cdp_%1_test.cjs
if errorlevel 1 set FAILED=%FAILED% %1
popd
exit /b 0

:summary
echo.
if "%FAILED%"=="" (
  echo ALL TESTS PASSED
  exit /b 0
) else (
  echo FAILED:%FAILED%
  exit /b 1
)

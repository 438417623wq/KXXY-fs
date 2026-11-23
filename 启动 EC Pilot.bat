@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EC Pilot
echo.
echo   EC Pilot - Fan and Keyboard Console
echo   ------------------------------------
set "PY=%~dp0runtime\py32\python.exe"
if not exist "%PY%" (
  echo   [!] bundled 32-bit runtime not found, falling back to system python
  set "PY=python"
)
"%PY%" "%~dp0app\main.py" %*
echo.
echo   process exited. press any key to close.
pause >nul

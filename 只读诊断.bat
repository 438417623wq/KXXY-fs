@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EC Pilot - Read-only diagnosis
echo.
echo   READ-ONLY diagnosis. Nothing will be written to the EC.
echo.
"%~dp0runtime\py32\python.exe" "%~dp0tools\ec_probe.py"
echo.
pause >nul

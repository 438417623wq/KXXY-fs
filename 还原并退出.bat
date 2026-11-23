@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EC Pilot - Restore all
echo.
echo   Restoring every fan / EC change back to firmware control...
echo.
"%~dp0runtime\py32\python.exe" "%~dp0tools\restore.py"
echo.
pause >nul

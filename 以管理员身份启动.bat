@echo off
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo   requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
  exit /b
)
set "PY=%~dp0runtime\py32\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0app\main.py" %*
echo.
pause >nul

@echo off
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo   requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
  exit /b
)
echo.
echo   Installing NTPORT driver (required for EC access).
echo   A reboot may be needed afterwards.
echo.
if exist "%~dp0driver_setup\NTPortDrvSetup.exe" (
  "%~dp0driver_setup\NTPortDrvSetup.exe"
) else if exist "%~dp0..\驱动程序_含说明\驱动程序_含说明\NTPortDrvSetup.exe" (
  "%~dp0..\驱动程序_含说明\驱动程序_含说明\NTPortDrvSetup.exe"
) else (
  echo   [!] NTPortDrvSetup.exe not found. Please install it manually.
)
echo.
pause >nul

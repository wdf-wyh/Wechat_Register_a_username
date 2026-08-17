@echo off
cd /d "%~dp0"
title WechatFarm daily run

echo Starting wechat_farm daily run...
echo Do not close this window until it finishes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0daily_run.ps1" -Command run
set ERR=%ERRORLEVEL%

echo.
if %ERR%==0 (
  echo DONE  exit=0
  echo log: ..\logs\cron.log
) else (
  echo FAILED  exit=%ERR%
  echo log: ..\logs\cron.log
)
echo.
echo Press any key to close this window.
pause >nul
exit /b %ERR%

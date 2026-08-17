@echo off
cd /d "%~dp0"
title WechatFarm install daily task

net session >nul 2>&1
if not %errorlevel%==0 (
  echo Need Administrator, requesting UAC...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo Installing WechatFarm scheduled tasks...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_daily_task.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
  echo SUCCESS. Tasks:
  schtasks /Query /TN WechatFarm_DailyRun
  schtasks /Query /TN WechatFarm_DailyReport
  schtasks /Query /TN WechatFarm_WeeklyAdvance
) else (
  echo FAILED  exit=%ERR%
)
echo.
echo Press any key to close this window.
pause >nul
exit /b %ERR%

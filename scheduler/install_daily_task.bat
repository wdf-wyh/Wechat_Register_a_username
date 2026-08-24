@echo off
cd /d "%~dp0"
title WechatFarm install daily task
set "WF_DIR=%CD%"

net session >nul 2>&1
if not %errorlevel%==0 (
  echo Need Administrator, requesting UAC...
  echo If a new window does not appear, right-click this file and Run as administrator.
  rem Pass the Chinese path via env var + [char]34. Do not put "%~f0" inside
  rem powershell -Command: cmd quoting breaks and this window exits immediately.
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:ComSpec -Verb RunAs -ArgumentList @('/c', ('cd /d ' + [char]34 + $env:WF_DIR + [char]34 + ' && ' + [char]34 + $env:WF_DIR + '\' + '%~nx0' + [char]34))"
  if not %errorlevel%==0 (
    echo UAC launch failed. Right-click install_daily_task.bat - Run as administrator.
    pause
  )
  exit /b
)

echo.
echo Installing WechatFarm scheduled tasks...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "install_daily_task.ps1"
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

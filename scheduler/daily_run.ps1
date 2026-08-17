# Daily wechat_farm entry: pick Day N from registration_date, then main.py run.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scheduler\daily_run.ps1
#   powershell -ExecutionPolicy Bypass -File scheduler\daily_run.ps1 -Command report
#   powershell -ExecutionPolicy Bypass -File scheduler\daily_run.ps1 -Command advance
#
# Do not call cold-start-burst (it rewrites registration_date and uses compressed timing).

param(
    [ValidateSet("run", "report", "advance")]
    [string]$Command = "run"
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$LogDir = Join-Path $Root "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}
$CronLog = Join-Path $LogDir "cron.log"
$LockFile = Join-Path $LogDir "daily_$Command.lock"
$Python = Join-Path $Root "wechat_env\Scripts\python.exe"

function Write-CronLog {
    param([string]$Message)
    $line = "{0} | {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $CronLog -Value $line -Encoding UTF8
    Write-Host $line
}

if (-not (Test-Path $Python)) {
    Write-CronLog "[FAIL] python not found: $Python"
    exit 1
}

if (Test-Path $LockFile) {
    $oldPid = 0
    try {
        $oldPid = [int](Get-Content $LockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    } catch {
        $oldPid = 0
    }
    if ($oldPid -gt 0) {
        $alive = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($alive) {
            Write-CronLog "[SKIP] $Command already running (PID=$oldPid)"
            exit 0
        }
    }
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}

Set-Content -Path $LockFile -Value $PID -Encoding ASCII
try {
    Write-CronLog "[START] main.py $Command  (cwd=$Root)"

    $adb = Get-Command adb -ErrorAction SilentlyContinue
    if ($adb) {
        $devices = cmd /c "adb devices 2>&1"
        Write-CronLog ("adb devices:`n" + (($devices | Out-String).Trim()))
    } else {
        Write-CronLog "[WARN] adb not in PATH, python will resolve it"
    }

    & $Python main.py $Command
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }

    if ($code -eq 0) {
        Write-CronLog "[OK] main.py $Command exit=$code"
    } else {
        Write-CronLog "[FAIL] main.py $Command exit=$code  see logs\wechat_farm_*.log"
    }
    exit $code
}
finally {
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}

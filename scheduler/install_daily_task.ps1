# Register Windows scheduled tasks for daily wechat_farm.
#
# Install:
#   powershell -ExecutionPolicy Bypass -File scheduler\install_daily_task.ps1
#
# Uninstall:
#   powershell -ExecutionPolicy Bypass -File scheduler\install_daily_task.ps1 -Uninstall
#
# Uses COM Schedule.Service as the current user (no admin). If that still
# fails, run an elevated PowerShell and retry.

param(
    [switch]$Uninstall,
    [string]$RunAt = "07:00",
    [string]$ReportAt = "23:00",
    [string]$AdvanceAt = "08:00"
)

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Need Administrator."
    Write-Host "Double-click install_daily_task.bat and accept UAC,"
    Write-Host "or right-click PowerShell -> Run as administrator, then:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File scheduler\install_daily_task.ps1"
    exit 1
}

$Root = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "daily_run.ps1"
$Pwsh = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

$TaskNames = @(
    "WechatFarm_DailyRun",
    "WechatFarm_DailyReport",
    "WechatFarm_WeeklyAdvance"
)

function Get-TaskFolder {
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    return $service.GetFolder("\")
}

function Remove-FarmTasks {
    $folder = Get-TaskFolder
    foreach ($name in $TaskNames) {
        try {
            $folder.DeleteTask($name, 0)
            Write-Host "uninstalled: $name"
        } catch {
            # task missing is fine
        }
    }
}

if ($Uninstall) {
    Remove-FarmTasks
    Write-Host "all WechatFarm tasks removed"
    exit 0
}

if (-not (Test-Path $Runner)) {
    throw "missing $Runner"
}

function Parse-Hm {
    param([string]$Hm)
    $parts = $Hm.Split(":")
    return @{ Hour = [int]$parts[0]; Minute = [int]$parts[1] }
}

function New-StartBoundary {
    param([int]$Hour, [int]$Minute)
    $t = Get-Date -Hour $Hour -Minute $Minute -Second 0
    if ($t -lt (Get-Date)) {
        $t = $t.AddDays(1)
    }
    return $t.ToString("s")
}

function Register-FarmTask {
    param(
        [string]$Name,
        [string]$ArgCommand,
        [ValidateSet("Daily", "WeeklyMonday")]
        [string]$Kind,
        [string]$At
    )

    $hm = Parse-Hm $At
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    $folder = $service.GetFolder("\")
    $task = $service.NewTask(0)
    $task.RegistrationInfo.Description = "wechat_farm $ArgCommand"
    $task.Settings.Enabled = $true
    $task.Settings.AllowDemandStart = $true
    $task.Settings.StartWhenAvailable = $true
    $task.Settings.DisallowStartIfOnBatteries = $false
    $task.Settings.StopIfGoingOnBatteries = $false
    $task.Settings.ExecutionTimeLimit = "PT16H"
    $task.Settings.MultipleInstances = 2
    $task.Principal.LogonType = 3
    $task.Principal.RunLevel = 0
    $task.Principal.UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

    if ($Kind -eq "Daily") {
        $trigger = $task.Triggers.Create(2)
        $trigger.DaysInterval = 1
    } else {
        $trigger = $task.Triggers.Create(3)
        $trigger.DaysOfWeek = 2
        $trigger.WeeksInterval = 1
    }
    $trigger.StartBoundary = New-StartBoundary -Hour $hm.Hour -Minute $hm.Minute
    $trigger.Enabled = $true

    $action = $task.Actions.Create(0)
    $action.Path = $Pwsh
    $action.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Command $ArgCommand"
    $action.WorkingDirectory = $Root

    try {
        [void]$folder.RegisterTaskDefinition($Name, $task, 6, $null, $null, 3)
    } catch {
        throw ("failed to register {0}: {1}`nOpen PowerShell as Administrator and retry." -f $Name, $_.Exception.Message)
    }
    Write-Host "registered: $Name"
}

Remove-FarmTasks

Register-FarmTask -Name "WechatFarm_DailyRun" -ArgCommand "run" -Kind Daily -At $RunAt
Register-FarmTask -Name "WechatFarm_DailyReport" -ArgCommand "report" -Kind Daily -At $ReportAt
Register-FarmTask -Name "WechatFarm_WeeklyAdvance" -ArgCommand "advance" -Kind WeeklyMonday -At $AdvanceAt

Write-Host ""
Write-Host "done. keep Windows logged in, phone USB online, stay-awake in developer options."
Write-Host "try now:  powershell -ExecutionPolicy Bypass -File `"$Runner`" -Command run"
Write-Host "list:     Get-ScheduledTask WechatFarm_*"
Write-Host "log:      $Root\logs\cron.log"

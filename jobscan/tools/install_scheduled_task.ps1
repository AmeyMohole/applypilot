<#
.SYNOPSIS
  Registers (or removes) the Windows scheduled task that runs jobscan unattended.

.DESCRIPTION
  Creates jobscan-daily.vbs next to run_daily.py (it launches Python with no console window) and a task
  named "jobscan" that runs it at logon, at 10:00, 14:00 and 18:00. run_daily.py itself allows at most three
  runs a day, four hours apart, so extra triggers just exit. The task runs as you, only while you are
  logged in (LinkedIn capture needs your Chrome session), and starts a missed run as soon as possible.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\install_scheduled_task.ps1
  powershell -ExecutionPolicy Bypass -File tools\install_scheduled_task.ps1 -Python "C:\Python312\python.exe"
  powershell -ExecutionPolicy Bypass -File tools\install_scheduled_task.ps1 -Remove

  Stop sending without removing the task: create an empty file named AUTO_OFF in the jobscan folder.
#>
param(
    [string]$Python = (Get-Command python -ErrorAction SilentlyContinue).Source,
    [string]$TaskName = "jobscan",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # the jobscan folder

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed task '$TaskName'."
    } else {
        Write-Host "No task named '$TaskName'."
    }
    return
}

if (-not $Python -or -not (Test-Path $Python)) {
    throw "Python not found. Pass -Python <path to python.exe>."
}
if (-not (Test-Path (Join-Path $root "run_daily.py"))) {
    throw "run_daily.py not found in $root"
}

$vbs = Join-Path $root "jobscan-daily.vbs"
@"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$root"
sh.Run """$Python"" run_daily.py --startup", 0, False
"@ | Set-Content -Path $vbs -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`""
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
    (New-ScheduledTaskTrigger -Daily -At "10:00"),
    (New-ScheduledTaskTrigger -Daily -At "14:00"),
    (New-ScheduledTaskTrigger -Daily -At "18:00")
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
    -Description "jobscan: capture leads, sync the inbox, draft and send gated applications and follow-ups" -Force | Out-Null

Write-Host "Task '$TaskName' registered: at logon, 10:00, 14:00 and 18:00."
Write-Host "Python: $Python"
Write-Host "Check it: python doctor.py     Run now: Start-ScheduledTask -TaskName $TaskName"

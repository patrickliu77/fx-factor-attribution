<#
.SYNOPSIS
    Register the Windows scheduled task for the daily live run (SPEC_phase2
    1.2, gate D1).

.DESCRIPTION
    Paths are derived from the environment at run time, never hardcoded
    (CLAUDE.md rule 11).

    This machine runs on US Central Time (UTC-06:00, UTC-05:00 under DST), not
    Beijing time. The default 19:30 local is 20:30 ET: the day's FX daily bars
    closed at 17:00 ET, the US close-based factors (FRED DGS, VIX, BAA10Y)
    publish between 16:15 and 18:00 ET, and foreign yields land earlier, so
    every input is in place by then. That leaves roughly 12.5 hours of buffer
    before the 09:00 ET cutoff the next morning, enough to absorb slow sources
    and the retry schedule (every 15 minutes, at most 3 attempts).

    An early-morning slot (06:00 local, 07:00 ET) was rejected: it compresses
    the buffer to 2 hours and buys no real freshness for a system that reports
    the previous FX daily bar. Both slots report the same closed session.

    Two settings matter and are set here: wake the computer to run the task,
    and start as soon as possible after a missed schedule. The latter compounds
    with the automatic gap backfill in SPEC_phase2 1.3, so a missed run heals
    itself.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ops\register_task.ps1
    powershell -ExecutionPolicy Bypass -File ops\register_task.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "fxdash-live",
    [string]$At = "19:30",
    [string]$Python = "$env:USERPROFILE\miniconda3\python.exe"
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo "src\fxdash\run.py"))) {
    throw "src\fxdash\run.py not found under $repo; run this script from inside the repository."
}
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python. Pass -Python to point at it. Do not use the default python on PATH; that is another project's virtual environment."
}

Write-Host "Repo   : $repo"
Write-Host "Python : $Python"
Write-Host "Time   : daily at $At local time"

# Wrapped in cmd so PYTHONPATH can be set; logs land in outputs/logs/
$logDir = Join-Path $repo "outputs\logs"
$command = "set PYTHONPATH=src && `"$Python`" -W ignore -m fxdash.run --mode live " +
           ">> `"$logDir\live.log`" 2>&1"

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c $command" -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

# New-ScheduledTaskSettingsSet defaults IdleSettings to StopOnIdleEnd=true,
# i.e. "stop the task when the computer is no longer idle". That is what killed
# the 2026-08-29 run: the task started at 19:30:01, someone touched the
# keyboard at 19:30:38, and the task took a Ctrl+C exit (0xC000013A) leaving
# only a ^C in the log. Somebody is usually at the machine at 19:30, so this
# default fails the task often, and silently.
$settings.IdleSettings.StopOnIdleEnd = $false
$settings.IdleSettings.RestartOnIdle = $false

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task")) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "FX factor attribution daily live run" -Force | Out-Null
    Write-Host "`nRegistered: $TaskName"

    # Read the full XML back and check the key entries. Checking only the
    # entries this script sets is not enough: StopOnIdleEnd, the one that
    # caused the 2026-08-29 failure, was a default outside that list, so the
    # whole XML is printed for inspection.
    $xml = Export-ScheduledTask -TaskName $TaskName
    $expect = @{
        "StopOnIdleEnd"            = "false"
        "DisallowStartIfOnBatteries" = "false"
        "StopIfGoingOnBatteries"   = "false"
        "StartWhenAvailable"       = "true"
        "WakeToRun"                = "true"
    }
    Write-Host "`nPost-registration check:"
    $bad = 0
    foreach ($k in $expect.Keys) {
        if ($xml -match "<$k>([^<]*)</$k>") {
            $got = $Matches[1]
            $ok = ($got -eq $expect[$k])
            if (-not $ok) { $bad++ }
            Write-Host ("  [{0}] {1,-26} = {2}" -f $(if ($ok) {"OK"} else {"!!"}), $k, $got)
        } else {
            Write-Host ("  [??] {0,-26} not found in XML" -f $k)
        }
    }
    Write-Host $(if ($bad -eq 0) { "  all key entries as expected" } else { "  !! $bad entries differ, please check" })
    Write-Host "`nFull XML:"
    Write-Host $xml
    Write-Host "`n  Show status : Get-ScheduledTaskInfo -TaskName $TaskName"
    Write-Host "  Unregister  : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
}

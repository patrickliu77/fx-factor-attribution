<#
.SYNOPSIS
    Register the daily static-site publish (SPEC_web section 7).

.DESCRIPTION
    The third task, separate from the attribution pipeline and the narrative run for
    the same reason those two are separate: this one goes online (git push) and can
    fail on its own, and a publish failure must not turn either of the other two red.

    It runs at 20:45 local: the pipeline (19:30) finishes in about five minutes, the
    narrative run (20:15) in seconds on a quiet day and a couple of minutes when it
    generates, so 20:45 leaves ample slack and the page always carries the evening's
    contract and commentary.

    Logs go to outputs/logs/publish.log in UTF-8. Credentials for the push come from
    Git Credential Manager's user-level store; nothing is stored in this script.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ops\register_publish_task.ps1 -WhatIf
    powershell -ExecutionPolicy Bypass -File ops\register_publish_task.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "fxdash-publish",
    [string]$At = "20:45"
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$publish = Join-Path $repo "ops\publish.ps1"
if (-not (Test-Path $publish)) {
    throw "ops\publish.ps1 not found under $repo; run this script from inside the repository."
}

Write-Host "Repo   : $repo"
Write-Host "Script : $publish"
Write-Host "Time   : daily at $At local time (after the 19:30 pipeline and the 20:15 narrative run)"

$logDir = Join-Path $repo "outputs\logs"
# Wrapped in cmd so the log redirect works the same way as the other two tasks.
# publish.ps1 resolves the interpreter itself.
$command = "set PYTHONIOENCODING=utf-8 && powershell -NoProfile -ExecutionPolicy Bypass " +
           "-File `"$publish`" >> `"$logDir\publish.log`" 2>&1"

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c $command" -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartInterval (New-TimeSpan -Minutes 20) `
    -RestartCount 2 `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

# Same trap as the other two scripts: New-ScheduledTaskSettingsSet defaults
# IdleSettings to StopOnIdleEnd=true, so the task is killed once the computer is no
# longer idle (0xC000013A, leaving only a ^C in the log). Off from the start.
$settings.IdleSettings.StopOnIdleEnd = $false
$settings.IdleSettings.RestartOnIdle = $false

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task")) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "FX dashboard static site publish (GitHub Pages)" -Force | Out-Null
    Write-Host "`nRegistered: $TaskName"

    # Export the full XML after registering. Reading back only the entries listed
    # here is not enough: StopOnIdleEnd, the one that failed on 2026-08-29, is
    # precisely a default that is not in the list.
    $xml = Export-ScheduledTask -TaskName $TaskName
    $expect = @{
        "StopOnIdleEnd"              = "false"
        "DisallowStartIfOnBatteries" = "false"
        "StopIfGoingOnBatteries"     = "false"
        "StartWhenAvailable"         = "true"
        "WakeToRun"                  = "true"
    }
    Write-Host "`nRegistration check:"
    $bad = 0
    foreach ($k in $expect.Keys) {
        if ($xml -match "<$k>([^<]*)</$k>") {
            $got = $Matches[1]
            $ok = ($got -eq $expect[$k])
            if (-not $ok) { $bad++ }
            Write-Host ("  [{0}] {1,-28} = {2}" -f $(if ($ok) {"OK"} else {"!!"}), $k, $got)
        } else {
            Write-Host ("  [??] {0,-28} not found in XML" -f $k)
        }
    }
    Write-Host $(if ($bad -eq 0) { "  all key entries as expected" } else { "  !! $bad entries differ" })
    Write-Host "`nFull XML:"
    Write-Host $xml
    Write-Host "`n  Show status : Get-ScheduledTaskInfo -TaskName $TaskName"
    Write-Host "  Unregister  : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
}

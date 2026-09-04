<#
.SYNOPSIS
    Register the daily run of the narrative layer (SPEC_phase3 2.2, decision
    D3).

.DESCRIPTION
    A task kept separate from the attribution pipeline, which is the point of
    D3: a narrative failure must not make the pipeline look broken, and network
    or LLM flakiness must not turn the 19:30 run red.

    It runs at 20:15 local, after the 19:30 pipeline. A full pipeline recompute
    takes about 10 minutes, so 20:15 leaves roughly 35 minutes of slack. Even
    if the pipeline runs long, the narrative layer just reads the previous
    day's contract: the artifact is still written, one day behind, and the next
    run catches up.

    Logging is explicitly UTF-8. The pipeline's live.log is GBK (the cmd.exe
    console code page), a legacy that would need the task re-registered to
    change; this task is new, so it is set correctly from the start.

    Credentials come from the environment only: GEMINI_API_KEY must be a USER
    level environment variable for the scheduler to see it. The key appears
    neither in this script nor in any artifact.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ops\register_narrative_task.ps1 -WhatIf
    powershell -ExecutionPolicy Bypass -File ops\register_narrative_task.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "fxdash-narrative",
    [string]$At = "20:15",
    [string]$Python = "$env:USERPROFILE\miniconda3\python.exe"
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo "src\fxdash\narrative\run.py"))) {
    throw "src\fxdash\narrative\run.py not found under $repo. Run this script from the repository root."
}
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python. Pass -Python to point at it. Do not use the default python on PATH; that is another project's virtual environment."
}

Write-Host "Repo   : $repo"
Write-Host "Python : $Python"
Write-Host "Time   : daily at $At local time (after the 19:30 pipeline)"

# Credential check. The scheduler reads USER level environment variables; one
# set inside a session does not count.
$userKey = [Environment]::GetEnvironmentVariable("GEMINI_API_KEY", "User")
if ([string]::IsNullOrWhiteSpace($userKey)) {
    Write-Host ""
    Write-Host "  !! GEMINI_API_KEY is not a user level environment variable." -ForegroundColor Yellow
    Write-Host "     The task still registers, but every run fails at generation and writes a failure record." -ForegroundColor Yellow
    Write-Host "     To set it: setx GEMINI_API_KEY <key>, then restart the terminal." -ForegroundColor Yellow
} else {
    Write-Host "Key    : GEMINI_API_KEY found in user environment (length $($userKey.Length), value not printed)"
}

$logDir = Join-Path $repo "outputs\logs"
# PYTHONIOENCODING is set to utf-8 explicitly: the pipeline's GBK live.log is
# a legacy this new task does not repeat
$command = "set PYTHONPATH=src && set PYTHONIOENCODING=utf-8 && " +
           "`"$Python`" -W ignore -m fxdash.narrative.run " +
           ">> `"$logDir\narrative.log`" 2>&1"

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
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

# Same trap as register_task.ps1: New-ScheduledTaskSettingsSet defaults
# IdleSettings to StopOnIdleEnd=true, so the task is killed once the computer
# is no longer idle (0xC000013A, leaving only a ^C in the log). That actually
# happened to the attribution task on 2026-08-29, so it is off from the
# start here.
$settings.IdleSettings.StopOnIdleEnd = $false
$settings.IdleSettings.RestartOnIdle = $false

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task")) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "FX narrative layer daily run (Phase 3)" -Force | Out-Null
    Write-Host "`nRegistered: $TaskName"

    # Export the full XML after registering. Reading back only the entries
    # listed here is not enough: StopOnIdleEnd, the one that failed on
    # 2026-08-29, is precisely a default that is not in the list.
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

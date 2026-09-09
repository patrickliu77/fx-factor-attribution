<#
.SYNOPSIS
    Add a windowless login and periodic catch-up task, without changing morning tasks.
.DESCRIPTION
    Runs two minutes after this user's login and every 15 minutes while available.
    Does not wake the computer. Python permits catch-up only after 09:05 New York
    on weekdays. Existing dated text is reused; generation is claimed once per day.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$Python = "", [string]$TaskName = "fxdash-catchup")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $env:USERPROFILE "miniconda3\pythonw.exe" }
if (-not (Test-Path -LiteralPath $Python) -or (Split-Path -Leaf $Python) -ne 'pythonw.exe') {
    throw 'Pass the windowless pythonw.exe interpreter.'
}
$wrapper = Join-Path $repo 'ops\run_catchup_task.py'
if (-not (Test-Path -LiteralPath $wrapper)) { throw 'Catch-up entry is missing.' }
$taskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$loginTrigger.Delay = 'PT2M'
$periodic = New-ScheduledTaskTrigger -Daily -At '00:00'
$pattern = New-ScheduledTaskTrigger -Once -At (Get-Date).AddDays(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 1)
$periodic.Repetition = $pattern.Repetition
$periodic.Repetition.StopAtDurationEnd = $false
$action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $wrapper + '"') -WorkingDirectory $repo
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
$settings.IdleSettings.StopOnIdleEnd = $false
$settings.IdleSettings.RestartOnIdle = $false
if ($PSCmdlet.ShouldProcess($TaskName, 'Register login and periodic catch-up checks, without waking the PC')) {
    $audit = Join-Path $repo ('outputs\catchup-acceptance\task-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $audit -ErrorAction Stop | Out-Null
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        [System.IO.File]::WriteAllText((Join-Path $audit 'before.xml'), (Export-ScheduledTask -TaskName $TaskName), [System.Text.Encoding]::Unicode)
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($loginTrigger,$periodic) `
        -Settings $settings -Principal $principal -Description 'FX dated catch-up briefing after login, no wake, no audio' -Force | Out-Null
    $installed = Get-ScheduledTask -TaskName $TaskName
    $xmlText = Export-ScheduledTask -TaskName $TaskName
    [System.IO.File]::WriteAllText((Join-Path $audit 'installed.xml'), $xmlText, [System.Text.Encoding]::Unicode)
    $xml = [xml]$xmlText
    if ($installed.Settings.WakeToRun -or $installed.Actions.Execute -ne $Python -or
        $installed.Principal.LogonType.ToString() -ne 'Interactive' -or
        $xml.Task.Triggers.LogonTrigger.Delay -ne 'PT2M' -or
        $xml.Task.Triggers.CalendarTrigger.Repetition.Interval -ne 'PT15M' -or
        $xml.Task.Settings.MultipleInstancesPolicy -ne 'IgnoreNew') {
        throw "Installed task verification failed; inspect $audit"
    }
    Write-Output 'Verified: login delay 2 minutes, periodic check 15 minutes, windowless, interactive login, no wake.'
    Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime,LastTaskResult,NextRunTime
}

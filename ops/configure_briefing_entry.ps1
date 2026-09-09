<#
.SYNOPSIS
    Switch only the existing morning task's action to a windowless Python entry.
.DESCRIPTION
    Preserves the installed trigger, principal and settings. Saves the complete
    previous XML locally before changing anything. Does not run the task.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$Python = "")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$taskName = "fxdash-briefing"
if (-not $Python) { $Python = Join-Path $env:USERPROFILE "miniconda3\pythonw.exe" }
if (-not (Test-Path -LiteralPath $Python)) { throw "Windowless interpreter is unavailable." }
$wrapper = Join-Path $repo "ops\run_briefing_task.py"
if (-not (Test-Path -LiteralPath $wrapper)) { throw "Morning wrapper is unavailable." }
$beforeText = Export-ScheduledTask -TaskName $taskName
$before = [xml]$beforeText
$action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $wrapper + '"') -WorkingDirectory $repo
if ($PSCmdlet.ShouldProcess($taskName, "Replace the action only; keep its current schedule and settings")) {
    $auditDir = Join-Path $repo ("outputs\operations-acceptance\task-entry-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Path $auditDir -ErrorAction Stop | Out-Null
    $backup = Join-Path $auditDir "before.xml"
    [System.IO.File]::WriteAllText($backup, $beforeText, [System.Text.Encoding]::Unicode)
    Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
    $afterText = Export-ScheduledTask -TaskName $taskName
    $after = [xml]$afterText
    [System.IO.File]::WriteAllText((Join-Path $auditDir "after.xml"), $afterText, [System.Text.Encoding]::Unicode)
    foreach ($section in @("Triggers", "Settings", "Principals")) {
        if ($before.Task.$section.OuterXml -ne $after.Task.$section.OuterXml) {
            throw "Unexpected change to $section. Previous definition: $backup"
        }
    }
    if ($after.Task.Actions.Exec.Command -ne $Python -or $after.Task.Actions.Exec.Arguments -ne ('"' + $wrapper + '"')) {
        throw "Task action verification failed. Previous definition: $backup"
    }
    Write-Output "Verified: action updated; triggers, settings and principal unchanged."
    Write-Output "Backup: $backup"
    Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, LastTaskResult, NextRunTime
}

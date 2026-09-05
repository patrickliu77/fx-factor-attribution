<#
.SYNOPSIS
    Register a five-minute clock gate for the 09:00 New York text briefing.
.DESCRIPTION
    Python evaluates America/New_York, including DST. Outside 08:50..10:00 ET
    on weekdays the command exits without loading data, networking or writing.
    Collection starts at 08:50; publication starts at 09:00. Late starts cannot
    fabricate a morning input packet. Existing evening tasks are unchanged.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = "fxdash-briefing", [string]$Python = "")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $env:USERPROFILE "miniconda3\python.exe" }
if (-not (Test-Path -LiteralPath $Python)) { throw "Pass -Python with the project interpreter." }
& $Python -c "from zoneinfo import ZoneInfo; import pandas, fastapi; ZoneInfo('America/New_York')"
if ($LASTEXITCODE -ne 0) { throw "Interpreter dependencies or tzdata are missing." }
$logDir = Join-Path $repo "outputs\logs"
$command = "set PYTHONPATH=src && set PYTHONIOENCODING=utf-8 && " +
           "`"$Python`" -m fxdash.narrative.morning_dispatch >> `"$logDir\briefing.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $command" -WorkingDirectory $repo
# A bounded UTC window covers both NY offsets without waking the PC all night.
# 12:50..15:00 UTC covers 08:50..10:00 in both EST and EDT. The Python gate makes
# the extra hour idle. Explicit Z avoids dependence on the computer's time zone.
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "12:50"
$trigger.StartBoundary = [DateTime]::UtcNow.Date.AddHours(12).AddMinutes(50).ToString("yyyy-MM-ddTHH:mm:ss'Z'")
$pattern = New-ScheduledTaskTrigger -Once -At (Get-Date).AddDays(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Minutes 130)
$trigger.Repetition = $pattern.Repetition
$trigger.Repetition.StopAtDurationEnd = $false
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -MultipleInstances IgnoreNew
$settings.IdleSettings.StopOnIdleEnd = $false
$settings.IdleSettings.RestartOnIdle = $false
Write-Host "Briefing: 08:50 collection, 09:00 publication, America/New_York."
Write-Host "Five-minute morning clock gate; 12:50..15:00 UTC weekdays. No audio."
Write-Host "Keep the user signed in. Wake settings cannot start a powered-off computer."
if ($PSCmdlet.ShouldProcess($TaskName, "Register the text briefing clock gate")) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "FX morning text briefing, New York time" -Force | Out-Null
    $registered = [xml](Export-ScheduledTask -TaskName $TaskName)
    $ns = New-Object System.Xml.XmlNamespaceManager($registered.NameTable)
    $ns.AddNamespace("t", "http://schemas.microsoft.com/windows/2004/02/mit/task")
    foreach ($check in @(@("CalendarTrigger/t:Repetition/t:Interval","PT5M"),@("CalendarTrigger/t:Repetition/t:Duration","PT2H10M"),@("CalendarTrigger/t:Repetition/t:StopAtDurationEnd","false"),@("StopOnIdleEnd","false"),@("WakeToRun","true"),@("StartWhenAvailable","true"),@("MultipleInstancesPolicy","IgnoreNew"))) {
        $node = $registered.SelectSingleNode("//t:$($check[0])", $ns)
        if (-not $node -or $node.InnerText -ne $check[1]) { throw "Registration verification failed: $($check[0])" }
    }
    # Windows may serialize Z as an equivalent explicit local UTC offset.
    $anchor = [DateTimeOffset]::Parse($registered.SelectSingleNode("//t:StartBoundary", $ns).InnerText)
    if ($anchor.UtcDateTime.ToString("HH:mm:ss") -ne "12:50:00") { throw "Unexpected UTC anchor" }
    Write-Host "Registered and verified: $TaskName"
}

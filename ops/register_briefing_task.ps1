<#
.SYNOPSIS
    Register a five-minute clock gate for the 09:00 New York briefing.
.DESCRIPTION
    Python evaluates America/New_York, including DST. Outside 08:50..10:00 ET
    on weekdays the command writes a local clock observation only. No networking,
    generation or publishing. A missed morning window is recorded as idle;
    the separate catch-up task handles an eligible late edition.
    Collection starts at 08:50; publication starts at 09:00. Late starts cannot
    fabricate a morning input packet. Existing evening tasks are unchanged.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = "fxdash-briefing", [string]$Python = "")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$resolved = & (Join-Path $PSScriptRoot 'setup_runtime.ps1') -ResolveOnly -Python $Python
$Python = $resolved.Path
$logDir = Join-Path $repo "outputs\logs"
$windowlessPython = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (-not (Test-Path -LiteralPath $windowlessPython)) { throw "pythonw.exe is required for a windowless morning task." }
$action = New-ScheduledTaskAction -Execute $windowlessPython `
    -Argument ('"' + (Join-Path $repo "ops\run_briefing_task.py") + '"') -WorkingDirectory $repo
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
Write-Host "Five-minute morning clock gate; 12:50..15:00 UTC weekdays."
Write-Host "Inside the delivery window, configured generation, audio, publishing and email may run."
Write-Host "Keep the user signed in. Wake settings cannot start a powered-off computer."
if ($PSCmdlet.ShouldProcess($TaskName, "Register the briefing and delivery clock gate")) {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "FX morning briefing and configured delivery, New York time" -Force | Out-Null
    $registered = [xml](Export-ScheduledTask -TaskName $TaskName)
    $ns = New-Object System.Xml.XmlNamespaceManager($registered.NameTable)
    $ns.AddNamespace("t", "http://schemas.microsoft.com/windows/2004/02/mit/task")
    foreach ($check in @(@("CalendarTrigger/t:Repetition/t:Interval","PT5M"),@("CalendarTrigger/t:Repetition/t:Duration","PT2H10M"),@("StopOnIdleEnd","false"),@("WakeToRun","true"),@("StartWhenAvailable","true"),@("MultipleInstancesPolicy","IgnoreNew"))) {
        $node = $registered.SelectSingleNode("//t:$($check[0])", $ns)
        if (-not $node -or $node.InnerText -ne $check[1]) { throw "Registration verification failed: $($check[0])" }
    }
    # The XML omits StopAtDurationEnd when it has the schema default false.
    if ((Get-ScheduledTask -TaskName $TaskName).Triggers.Repetition.StopAtDurationEnd) {
        throw "The repetition window must not terminate an in-flight publication."
    }
    # Windows may serialize Z as an equivalent explicit local UTC offset.
    $anchor = [DateTimeOffset]::Parse($registered.SelectSingleNode("//t:StartBoundary", $ns).InnerText)
    if ($anchor.UtcDateTime.ToString("HH:mm:ss") -ne "12:50:00") { throw "Unexpected UTC anchor" }
    Write-Host "Registered and verified: $TaskName"
}

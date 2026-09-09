<# Replace only the live action; retain triggers, account, power and retry policy. #>
[CmdletBinding(SupportsShouldProcess=$true)]
param([string]$TaskName='fxdash-live',[string]$Python='')
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
if (-not $Python) {$Python=Join-Path $env:USERPROFILE 'miniconda3\pythonw.exe'}
if (-not (Test-Path -LiteralPath $Python) -or (Split-Path -Leaf $Python) -ne 'pythonw.exe') {throw 'A windowless pythonw.exe is required.'}
$entry=Join-Path $repo 'ops\run_live_task.py'
if (-not (Test-Path -LiteralPath $entry)) {throw 'Live supervisor entry is missing.'}
$beforeText=Export-ScheduledTask -TaskName $TaskName
[xml]$before=$beforeText
if ($PSCmdlet.ShouldProcess($TaskName,'Use the windowless live supervisor; preserve all other settings')) {
    $audit=Join-Path $repo ('outputs\reliability-20260908\task-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $audit | Out-Null
    [IO.File]::WriteAllText((Join-Path $audit 'before.xml'),$beforeText,[Text.Encoding]::Unicode)
    $priorInfo=Get-ScheduledTaskInfo -TaskName $TaskName
    $priorState=(Get-ScheduledTask -TaskName $TaskName).State.ToString()
    if ($priorState -eq 'Running') {throw 'Wait for the existing live task to finish before changing its entry.'}
    # Preserve a genuine pre-supervisor failure as an observed scheduler result.
    # It is not a synthetic start/finish pair and has no invented completion time.
    $latestPath=Join-Path $repo 'outputs\task_runs\live\latest.json'
    if ($priorInfo.LastTaskResult -ne 0 -and $priorInfo.LastRunTime.Year -ge 2020 -and -not (Test-Path -LiteralPath $latestPath)) {
        $runId='observed-'+(Get-Date -Format 'yyyyMMddTHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)
        $observation=@{schema_version=1;run_id=$runId;source='task_scheduler_observation';
            state=$(if ([uint64]$priorInfo.LastTaskResult -ge 2147483648) {'crashed'} else {'failed'});
            started_at=$priorInfo.LastRunTime.ToUniversalTime().ToString('o');
            observed_at=[DateTime]::UtcNow.ToString('o');exit_code=$priorInfo.LastTaskResult;
            exit_hex=('0x{0:x8}' -f $priorInfo.LastTaskResult)} | ConvertTo-Json
        $observationDir=Join-Path (Split-Path -Parent $latestPath) $runId
        New-Item -ItemType Directory -Path $observationDir -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $observationDir 'observation.json'),$observation,[Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($latestPath,$observation,[Text.UTF8Encoding]::new($false))
    }
    $action=New-ScheduledTaskAction -Execute $Python -Argument ('"'+$entry+'"') -WorkingDirectory $repo
    Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
    $afterText=Export-ScheduledTask -TaskName $TaskName
    [xml]$after=$afterText
    [IO.File]::WriteAllText((Join-Path $audit 'installed.xml'),$afterText,[Text.Encoding]::Unicode)
    foreach ($name in @('Triggers','Principals','Settings')) {
        if ($before.Task.$name.OuterXml -ne $after.Task.$name.OuterXml) {throw "Unexpected task change: $name; inspect $audit"}
    }
    if ($after.Task.Actions.Exec.Command -ne $Python) {throw 'Unexpected installed executable'}
    Write-Output 'Verified windowless action. Triggers, principal, power settings and retry policy unchanged.'
}

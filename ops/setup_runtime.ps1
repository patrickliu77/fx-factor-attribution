<#
.SYNOPSIS
    Create the project environment on a persistent Python 3.13 installation.
.DESCRIPTION
    Installs the existing exact requirements. Never registers or runs tasks.
    -ResolveOnly is shared by registration scripts and makes no changes.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$BasePython = '',
    [string]$WheelCache = '',
    [switch]$ResolveOnly,
    [string]$Python = '',
    [switch]$Windowless
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$probe = Join-Path $PSScriptRoot 'check_delivery_runtime.py'
$projectPython = Join-Path $repo '.venv\Scripts\python.exe'

function Read-Runtime([string]$Executable, [switch]$BaseOnly) {
    $arguments = @($probe, '--repo', $repo, '--no-save')
    $arguments += $(if ($BaseOnly) { '--base-only' } else { '--runtime-only' })
    $response = @(& $Executable @arguments)
    if ($LASTEXITCODE -ne 0) { throw 'Runtime probe failed; no tasks were changed.' }
    return (($response -join '') | ConvertFrom-Json).runtime
}

if ($ResolveOnly) {
    $selected = $(if ($Python) { $Python } else { $projectPython })
    if ((Split-Path -Leaf $selected) -eq 'pythonw.exe') {
        $selected = Join-Path (Split-Path -Parent $selected) 'python.exe'
    }
    if (-not (Test-Path -LiteralPath $selected -PathType Leaf)) {
        throw 'Project Python is missing. Run ops/setup_runtime.ps1 first or pass a persistent -Python.'
    }
    $selected = (Resolve-Path -LiteralPath $selected).Path
    $health = Read-Runtime $selected
    if ($health.state -ne 'ready') { throw ('Python runtime is not ready: ' + $health.state) }
    if ($Windowless) {
        $selected = Join-Path (Split-Path -Parent $selected) 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $selected -PathType Leaf)) { throw 'pythonw.exe is required.' }
    }
    return @{ Path = $selected; Source = $(if ($Python) { '-Python' } else { 'project .venv' }) }
}

if (-not $BasePython) {
    $candidates = @()
    foreach ($name in @('python', 'python3')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\fxdash-python-3.13\python.exe'
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        if ($candidate -like '*\Microsoft\WindowsApps\*') { continue }
        try { $candidateHealth = Read-Runtime $candidate -BaseOnly } catch { continue }
        if ($candidateHealth.state -eq 'ready') { $BasePython = $candidate; break }
    }
}
if (-not $BasePython -or -not (Test-Path -LiteralPath $BasePython -PathType Leaf)) {
    throw 'Pass -BasePython with a persistent Python 3.13 installation.'
}
$baseHealth = Read-Runtime $BasePython -BaseOnly
if ($baseHealth.state -ne 'ready') { throw ('Base Python is unsuitable: ' + $baseHealth.state) }
$environmentPath = Join-Path $repo '.venv'
if ((Test-Path -LiteralPath $environmentPath) -and -not (Test-Path -LiteralPath $projectPython)) {
    throw 'An incomplete .venv already exists; no directory will be overwritten or removed.'
}
if (Test-Path -LiteralPath $projectPython) {
    $current = Read-Runtime $projectPython -BaseOnly
    if ($current.state -ne 'ready') { throw 'Existing .venv has an unsuitable base; recreate it explicitly.' }
}
Write-Host ('Base Python: ' + $BasePython)
Write-Host ('Project environment: ' + $environmentPath)
if ($PSCmdlet.ShouldProcess($environmentPath, 'Create persistent environment and install existing exact pins')) {
    if (-not (Test-Path -LiteralPath $projectPython)) {
        & $BasePython -m venv --without-scm-ignore-files $environmentPath
        if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed.' }
    }
    $arguments = @('-m', 'pip', '--disable-pip-version-check', 'install', '--quiet', '--index-url', 'https://pypi.org/simple',
                   '--requirement', (Join-Path $repo 'requirements.txt'))
    if ($WheelCache) { $arguments += @('--cache-dir', $WheelCache) }
    & $projectPython @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed.' }
    & $projectPython -m pip --disable-pip-version-check check
    if ($LASTEXITCODE -ne 0) { throw 'Installed dependency constraints failed.' }
    $health = Read-Runtime $projectPython
    if ($health.state -ne 'ready') { throw ('Installed runtime is not ready: ' + $health.state) }
    Write-Host 'Verified: persistent Python 3.13, exact dependency pins, timezone data. No tasks changed.'
}

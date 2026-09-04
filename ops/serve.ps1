<#
.SYNOPSIS
    Start the localhost dashboard (SPEC_web section 4).

.DESCRIPTION
    Pure downstream consumer: reads outputs/ only. Hot reload picks up each
    19:30 run automatically, so the service never needs restarting.
    Single worker (multiple workers would each hold their own snapshot and
    reload out of step); bound to 127.0.0.1 only (no firewall prompt); no
    --reload (watchfiles is unreliable on paths with non-ASCII characters).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ops\serve.ps1
    powershell -ExecutionPolicy Bypass -File ops\serve.ps1 -Port 9000
#>
param(
    [int]$Port = 8321,
    [string]$Python = "$env:USERPROFILE\miniconda3\python.exe"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo "src\fxdash\web\app.py"))) {
    throw "src\fxdash\web\app.py not found under $repo; run this script from inside the repository."
}
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python. Pass -Python to point at it; do not use the default python on PATH."
}

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo

Write-Host "Dashboard: http://127.0.0.1:$Port  (Ctrl+C to stop)"
& $Python -m uvicorn fxdash.web.app:create_app --factory `
    --host 127.0.0.1 --port $Port --workers 1 --log-level warning

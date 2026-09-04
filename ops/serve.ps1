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
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

# Interpreter resolution: an explicit -Python wins; otherwise the first python on
# PATH that can import this project's dependencies; otherwise the default miniconda
# location under the user profile. The probe imports rather than checking that a
# file exists, because a PATH python is often another project's environment or the
# Microsoft Store stub.
function Resolve-Python([string]$Explicit) {
    $ErrorActionPreference = "Continue"
    if ($Explicit) { return @{ Path = $Explicit; Source = "-Python" } }
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            $ok = $false
            try {
                & $cmd.Source -c "import pandas, pyarrow, fastapi, uvicorn" 2>$null | Out-Null
                $ok = ($LASTEXITCODE -eq 0)
            } catch { $ok = $false }
            if ($ok) { return @{ Path = $cmd.Source; Source = "PATH" } }
        }
    }
    return @{ Path = "$env:USERPROFILE\miniconda3\python.exe"; Source = "default location" }
}
$resolved = Resolve-Python $Python
$Python = $resolved.Path
$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo "src\fxdash\web\app.py"))) {
    throw "src\fxdash\web\app.py not found under $repo; run this script from inside the repository."
}
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python. Pass -Python <path> to point at an interpreter with this project's dependencies installed."
}

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $repo

Write-Host "Python    : $Python ($($resolved.Source))"
Write-Host "Dashboard : http://127.0.0.1:$Port  (Ctrl+C to stop)"
& $Python -m uvicorn fxdash.web.app:create_app --factory `
    --host 127.0.0.1 --port $Port --workers 1 --log-level warning

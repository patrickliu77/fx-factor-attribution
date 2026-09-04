<#
.SYNOPSIS
    Build the static site and force-push it to the public repository's gh-pages branch.

.DESCRIPTION
    The third scheduled task, fxdash-publish, 20:45 local, after the 19:30 pipeline
    and the 20:15 narrative run. A pure downstream consumer: it reads outputs/ and
    data/cache/, writes only site/, and never touches either status.json. A failure
    here is this task's own; the other two tasks and their heartbeats are unaffected.

    site/ is rebuilt from nothing on every run and pushed as a single commit with
    --force to an orphan branch, so the published history never grows. GitHub Pages
    serves that branch; the page reads build.json and computes its own age.

    -WhatIf builds the site (local, harmless) and skips the push.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File ops\publish.ps1 -WhatIf
    powershell -ExecutionPolicy Bypass -File ops\publish.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Remote = "https://github.com/patrickliu77/fx-factor-attribution.git",
    [string]$Branch = "gh-pages",
    [string]$Site = "",
    [string]$Python = "",
    [string]$AuthorName = "Peirui Liu",
    [string]$AuthorEmail = "peiruiliu0929@gmail.com"
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
                & $cmd.Source -c "import pandas, pyarrow, fastapi, httpx" 2>$null | Out-Null
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
if (-not (Test-Path (Join-Path $repo "src\fxdash\web\build.py"))) {
    throw "src\fxdash\web\build.py not found under $repo; run this script from inside the repository."
}
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python. Pass -Python <path> to point at an interpreter with this project's dependencies installed."
}
if (-not $Site) { $Site = Join-Path $repo "site" }

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$stamp] publish start"
Write-Host "Repo   : $repo"
Write-Host "Python : $Python ($($resolved.Source))"
Write-Host "Site   : $Site"
Write-Host "Remote : $Remote ($Branch)"

# ------------------------------------------------------------------ build
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
Push-Location $repo
try {
    & $Python -m fxdash.web.build --out $Site
    if ($LASTEXITCODE -ne 0) { throw "build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$manifestPath = Join-Path $Site "build.json"
if (-not (Test-Path $manifestPath)) { throw "build produced no build.json under $Site" }
$manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $manifest.files -or $manifest.files.Count -lt 1) { throw "build.json lists no api files" }
Write-Host "Built  : $($manifest.built_at)  as_of $($manifest.as_of)  $($manifest.files.Count) api files"

# ------------------------------------------------------------------- push
# A fresh repository every run: one commit, no history, nothing carried over.
if ($PSCmdlet.ShouldProcess("$Remote $Branch", "Force-push the site")) {
    $env:GCM_INTERACTIVE = "Never"
    $env:GIT_TERMINAL_PROMPT = "0"
    Push-Location $Site
    try {
        git init -q
        if ($LASTEXITCODE -ne 0) { throw "git init failed" }
        git symbolic-ref HEAD "refs/heads/$Branch"
        git add -A
        if ($LASTEXITCODE -ne 0) { throw "git add failed" }
        git -c "user.name=$AuthorName" -c "user.email=$AuthorEmail" commit -q -m "site build $($manifest.built_at)"
        if ($LASTEXITCODE -ne 0) { throw "git commit failed" }
        git push --force --quiet $Remote "HEAD:refs/heads/$Branch"
        if ($LASTEXITCODE -ne 0) { throw "git push failed with exit code $LASTEXITCODE" }
        $head = git rev-parse --short HEAD
        $remoteHead = git ls-remote $Remote "refs/heads/$Branch"
        Write-Host "Pushed : $head -> $Branch"
        Write-Host "Remote : $remoteHead"
    } finally {
        Pop-Location
    }
}
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$stamp] publish done"

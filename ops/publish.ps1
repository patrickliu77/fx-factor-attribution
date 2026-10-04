<#
.SYNOPSIS
    Build the static site and publish it to the public repository's gh-pages branch.

.DESCRIPTION
    The third scheduled task, fxdash-publish, 20:45 local, after the 19:30 pipeline
    and the 20:15 narrative run. A pure downstream consumer: it reads outputs/ and
    data/cache/, writes only site/, and never touches either status.json. A failure
    here is this task's own; the other two tasks and their heartbeats are unaffected.

    site/ is replaced after a complete build and pushed as a single commit with
    an explicit remote-head lease, so concurrent publishers cannot overwrite each
    other. GitHub Pages serves that branch; the page reads build.json for its age.

    -WhatIf builds and validates the site locally and skips Git and public probes.

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

function Invoke-CheckedGit([string[]]$Arguments) {
    $output = @(& git @Arguments)
    $gitExitCode = $LASTEXITCODE
    if ($gitExitCode -ne 0) {
        throw "git $($Arguments[0]) failed with exit code $gitExitCode"
    }
    return $output
}

function Read-RemoteHead([string]$Reference) {
    $rows = @(Invoke-CheckedGit -Arguments @("ls-remote", "--refs", "--", $Remote, $Reference))
    # A successful, empty response means the branch has not been created yet.
    # A failed probe must never be converted into an empty expected lease.
    if ($rows.Count -eq 0) { return "" }
    $pattern = '^([0-9a-f]{40}|[0-9a-f]{64})\t' + [regex]::Escape($Reference) + '$'
    if ($rows.Count -ne 1 -or $rows[0] -notmatch $pattern) {
        throw "git ls-remote returned an invalid or ambiguous head for $Reference"
    }
    return $Matches[1]
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
# The builder resolves relative output paths under the repository. Use that
# same absolute path for the lock, manifest reads and every Git operation.
if (-not [System.IO.Path]::IsPathRooted($Site)) { $Site = Join-Path $repo $Site }
$Site = [System.IO.Path]::GetFullPath($Site)

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$stamp] publish start"
Write-Host "Repo   : $repo"
Write-Host "Python : $Python ($($resolved.Source))"
Write-Host "Site   : $Site"
Write-Host "Remote : $Remote ($Branch)"

# ------------------------------------------------------------------ build
# The morning dispatcher and evening task share site/. A second writer must not
# remove files while the first writer is building or committing that directory.
$mutexBytes = [System.Text.Encoding]::UTF8.GetBytes($Site.ToLowerInvariant())
$mutexHash = [System.Security.Cryptography.SHA256]::Create()
$mutexSuffix = [System.BitConverter]::ToString($mutexHash.ComputeHash($mutexBytes)).Replace("-", "")
$mutexHash.Dispose()
$publishMutex = New-Object System.Threading.Mutex($false, ("Local\FXDashPublish-" + $mutexSuffix))
$ownsPublish = $false
try {
    try { $ownsPublish = $publishMutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $ownsPublish = $true }
    if (-not $ownsPublish) { throw "Another publish owns this site directory; retry later." }
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

# Reuse the cloud publisher's read-only identity and media-integrity gate. This
# also runs under -WhatIf; inspecting a candidate never publishes or sends mail.
Push-Location $repo
try {
    & $Python -m fxdash.cloud.publish --candidate $Site
    if ($LASTEXITCODE -ne 0) { throw "candidate validation failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

# ------------------------------------------------------------------- push
# A fresh repository every run: one commit, no history, nothing carried over.
if ($PSCmdlet.ShouldProcess("$Remote $Branch", "Publish the site with a remote-head lease")) {
    $env:GCM_INTERACTIVE = "Never"
    $env:GIT_TERMINAL_PROMPT = "0"
    Push-Location $Site
    try {
        $reference = "refs/heads/$Branch"
        Invoke-CheckedGit -Arguments @("check-ref-format", $reference) | Out-Null
        Invoke-CheckedGit -Arguments @("init", "-q") | Out-Null
        Invoke-CheckedGit -Arguments @("symbolic-ref", "HEAD", $reference) | Out-Null
        Invoke-CheckedGit -Arguments @("add", "-A") | Out-Null
        Invoke-CheckedGit -Arguments @("-c", "user.name=$AuthorName", "-c", "user.email=$AuthorEmail",
                                      "commit", "-q", "-m", "site build $($manifest.built_at)") | Out-Null
        $headRows = @(Invoke-CheckedGit -Arguments @("rev-parse", "--verify", "HEAD"))
        if ($headRows.Count -ne 1 -or $headRows[0] -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
            throw "git rev-parse returned an invalid full commit"
        }
        $head = $headRows[0]
        $expectedHead = Read-RemoteHead $reference
        Invoke-CheckedGit -Arguments @("push", "--quiet", "--force-with-lease=$($reference):$expectedHead",
                                      "--", $Remote, "HEAD:$reference") | Out-Null
        $remoteHead = Read-RemoteHead $reference
        if ($remoteHead -cne $head) {
            throw "Publication is unconfirmed: the remote head differs from the local commit."
        }
        Write-Host "Pushed : $head -> $Branch"
        Write-Host "Remote : $remoteHead"
    } finally {
        Pop-Location
    }
    # A Git push is not evidence that Pages has finished deploying. Record one
    # bounded public probe separately; later morning/catch-up checks can retry.
    Push-Location $repo
    try {
        & $Python -m fxdash.narrative.public_delivery
        if ($LASTEXITCODE -ne 0) { Write-Host 'Public delivery is pending or incomplete; see the separate delivery receipt.' }
    } finally {
        Pop-Location
    }
}
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "[$stamp] publish done"
} finally {
    if ($ownsPublish) { $publishMutex.ReleaseMutex() }
    $publishMutex.Dispose()
}

<#
.SYNOPSIS
    Store a user-provided Azure Speech key locally without echoing it.
.DESCRIPTION
    Does not create a subscription, call Azure, synthesize speech or change tasks.
    User environment variables are local plaintext storage, not a secret vault.
    Omit -Persist for a current-shell-only setup. Dot-source in that case.
    Add -EnableScheduled only after approving the audition and account quota.
#>
param([switch]$Persist, [switch]$EnableScheduled)
$ErrorActionPreference = 'Stop'
$speechRegion = (Read-Host 'Speech resource region (for example eastus)').Trim().ToLowerInvariant()
if ($speechRegion -notmatch '^[a-z][a-z0-9]{2,39}$') { throw 'Invalid region. Enter the region name, not an endpoint URL.' }
Write-Host 'At the hidden key prompt, paste with Shift+Insert. Use the region shown on the same Azure resource.'
$speechSecure = Read-Host 'Speech resource key (input hidden)' -AsSecureString
$speechPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($speechSecure)
try {
    $speechSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($speechPointer)
    if ([string]::IsNullOrWhiteSpace($speechSecret) -or $speechSecret.Length -gt 256 -or
        $speechSecret -match '[^\x21-\x7E]' -or $speechSecret -match '^\*+$') {
        throw 'Invalid key input. Copy the resource key again and paste with Shift+Insert; control characters are not accepted.'
    }
    [Environment]::SetEnvironmentVariable('AZURE_SPEECH_KEY', $speechSecret, 'Process')
    [Environment]::SetEnvironmentVariable('AZURE_SPEECH_REGION', $speechRegion, 'Process')
    if ($Persist) {
        [Environment]::SetEnvironmentVariable('AZURE_SPEECH_KEY', $speechSecret, 'User')
        [Environment]::SetEnvironmentVariable('AZURE_SPEECH_REGION', $speechRegion, 'User')
    }
    if ($EnableScheduled) {
        [Environment]::SetEnvironmentVariable('FXDASH_AUDIO', 'azure', 'Process')
        if ($Persist) { [Environment]::SetEnvironmentVariable('FXDASH_AUDIO', 'azure', 'User') }
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($speechPointer)
    $speechSecret = $null
    $speechSecure.Dispose()
}
Write-Host 'Speech settings saved. Azure authentication has not been checked; no network call was made.'
if (-not $EnableScheduled) { Write-Host 'The selected scheduled audio backend was left unchanged.' }
if ($Persist) { Write-Host 'Scheduled FX workers read the latest saved speech settings on each launch. Existing interactive shells keep their old environment.' }

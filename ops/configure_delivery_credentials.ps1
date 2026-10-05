<#
.SYNOPSIS
    Restore selected delivery credentials through hidden local prompts.
.DESCRIPTION
    Retains existing user settings unless -ReplaceExisting is supplied.
    Saves only the named settings to the Windows user environment (plaintext,
    not a secret vault). Does not call providers, send mail, generate speech,
    publish a site or register tasks. Run -WhatIf to inspect the selected names.
#>
[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [ValidateSet('FRED_API_KEY','BANXICO_TOKEN','GEMINI_API_KEY','BREVO_API_KEY','AZURE_SPEECH_KEY','AZURE_SPEECH_REGION')]
    [string[]]$Names = @('FRED_API_KEY','BANXICO_TOKEN','GEMINI_API_KEY','BREVO_API_KEY','AZURE_SPEECH_KEY','AZURE_SPEECH_REGION'),
    [switch]$ReplaceExisting,
    [switch]$EnableAzureAudio
)
$ErrorActionPreference = 'Stop'
$credentialNames = @($Names | Select-Object -Unique)
if (-not $PSCmdlet.ShouldProcess(($credentialNames -join ', '), 'Read hidden input and save selected user settings')) { return }
Write-Host 'Enter credentials in this local window only. Hidden prompts do not echo input.'
Write-Host 'Windows user environment storage is plaintext. Existing saved values are retained by default.'
$credentialValues = @{}
try {
    foreach ($credentialName in $credentialNames) {
        $credentialExisting = [Environment]::GetEnvironmentVariable($credentialName, 'User')
        if (-not $ReplaceExisting -and -not [string]::IsNullOrWhiteSpace($credentialExisting)) {
            Write-Host "$credentialName is already saved; retained."
            continue
        }
        if ($credentialName -eq 'AZURE_SPEECH_REGION') {
            $credentialValue = (Read-Host 'Azure Speech region (for example eastus)').Trim().ToLowerInvariant()
            if ($credentialValue -notmatch '^[a-z][a-z0-9]{2,39}$') { throw 'Invalid Azure region. No new values were saved.' }
        } else {
            $credentialSecure = Read-Host "$credentialName (input hidden; paste with Shift+Insert)" -AsSecureString
            $credentialPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credentialSecure)
            try {
                $credentialValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($credentialPointer)
                if ($credentialValue.Length -lt 10 -or $credentialValue.Length -gt 1024 -or
                    $credentialValue -match '[^\x21-\x7E]' -or $credentialValue -match '^\*+$') {
                    throw "Invalid $credentialName input. No new values were saved."
                }
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($credentialPointer)
                $credentialSecure.Dispose()
            }
        }
        $credentialValues[$credentialName] = $credentialValue
        $credentialValue = $null
    }
    if ($EnableAzureAudio) {
        foreach ($credentialRequired in @('AZURE_SPEECH_KEY','AZURE_SPEECH_REGION')) {
            if (-not $credentialValues.ContainsKey($credentialRequired) -and
                [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($credentialRequired, 'User'))) {
                throw 'Azure key and region are both required. No new values were saved.'
            }
        }
        $credentialValues['FXDASH_AUDIO'] = 'azure'
    }
    foreach ($credentialName in $credentialValues.Keys) {
        [Environment]::SetEnvironmentVariable($credentialName, $credentialValues[$credentialName], 'User')
        [Environment]::SetEnvironmentVariable($credentialName, $credentialValues[$credentialName], 'Process')
        Write-Host "$credentialName saved; authentication has not been verified."
    }
} finally {
    $credentialValue = $null
    $credentialExisting = $null
    $credentialValues.Clear()
}
Write-Host 'Open a new PowerShell window before checking readiness or registering tasks.'
Write-Host 'No provider request was made. Existing email sender, forms, lists and send receipts were retained.'

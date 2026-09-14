<#
.SYNOPSIS
    Configure an existing Brevo sender and two double-opt-in subscriber lists.
.DESCRIPTION
    No account creation, provider call, email send, task change or site publication.
    The key is stored in the user's local environment, which is not a secret vault.
#>
param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$emailRepo = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $env:USERPROFILE 'miniconda3/python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Pass -Python with the project interpreter path.' }
Write-Host 'Use dedicated English/Chinese lists populated only by double-opt-in forms. Do not import unconfirmed addresses.'
Write-Host 'Set the campaign unsubscribe footer, sender identity, postal details and account quota in Brevo first.'
$emailSettings = @{
    enabled = $true
    sender_id = [int](Read-Host 'Verified Brevo sender ID')
    sender_footer = Read-Host 'Sender identity and postal details for the email footer'
    forms = @{
        en = Read-Host 'English hosted double-opt-in form URL (https://...sibforms.com/serve/...)'
        zh = Read-Host 'Chinese hosted double-opt-in form URL'
    }
    lists = @{
        en = [int](Read-Host 'English confirmed-subscriber list ID')
        zh = [int](Read-Host 'Chinese confirmed-subscriber list ID')
    }
    double_opt_in_confirmed = $false
    quota_approved = $false
}
$emailApproval = Read-Host 'Type ENABLE after testing both confirmation and unsubscribe flows and approving the account quota/billing'
if ($emailApproval -cne 'ENABLE') { throw 'Not enabled. No settings changed.' }
$emailSettings.double_opt_in_confirmed = $true
$emailSettings.quota_approved = $true
Write-Host 'Paste the API key with Shift+Insert at the hidden prompt. Do not paste it into chat.'
$emailSecure = Read-Host 'Brevo API key (input hidden)' -AsSecureString
$emailPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($emailSecure)
try {
    $emailSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($emailPointer)
    if ($emailSecret.Length -lt 10 -or $emailSecret.Length -gt 512 -or $emailSecret -match '[^\x21-\x7E]' -or $emailSecret -match '^\*+$') {
        throw 'Invalid key input; paste with Shift+Insert.'
    }
    [Environment]::SetEnvironmentVariable('BREVO_API_KEY',$emailSecret,'User')
    [Environment]::SetEnvironmentVariable('BREVO_API_KEY',$emailSecret,'Process')
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($emailPointer)
    $emailSecret = $null
    $emailSecure.Dispose()
}
$env:PYTHONPATH = Join-Path $emailRepo 'src'
$env:PYTHONIOENCODING = 'utf-8'
$emailOldEncoding = $OutputEncoding
try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding
    $emailSettings | ConvertTo-Json -Depth 4 | & $Python -m fxdash.narrative.subscriptions --configure
    if ($LASTEXITCODE -ne 0) { throw 'Configuration was rejected. The stored key was retained; check form URLs and IDs.' }
} finally { $OutputEncoding = $emailOldEncoding }
Write-Host 'Saved and enabled for future eligible runs. No email or network request was sent by this helper.'
Write-Host 'The public form appears after the next site build. Confirmation and inbox delivery still need a real test.'

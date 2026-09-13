<# Offline TTS worker. Writes a new WAV only; never uses the speakers. #>
param(
    [Parameter(Mandatory=$true)][string]$TextFile,
    [Parameter(Mandatory=$true)][string]$WaveFile,
    [Parameter(Mandatory=$true)][ValidateSet('en','zh')][string]$Language
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$engine = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if (Test-Path -LiteralPath $WaveFile) { throw 'Destination already exists' }
    $culture = if ($Language -eq 'zh') { 'zh-CN' } else { 'en-US' }
    $voices = @($engine.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $culture })
    if ($voices.Count -eq 0) { throw 'Required voice unavailable' }
    $voice = $voices[0].VoiceInfo.Name
    $engine.SelectVoice($voice)
    $engine.Rate = 0
    $engine.SetOutputToWaveFile($WaveFile)
    $engine.Speak([IO.File]::ReadAllText($TextFile, [Text.Encoding]::UTF8))
    $engine.SetOutputToNull()
    @{voice=$voice; culture=$culture; rate=0} | ConvertTo-Json -Compress
} finally { $engine.Dispose() }

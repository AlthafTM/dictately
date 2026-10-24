# Generate local test speech using the Windows voice already installed.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$screenshotRoot = Split-Path $PSScriptRoot -Parent
$screenshotFixture = Join-Path $screenshotRoot 'qa/screenshot-command.wav'
New-Item -ItemType Directory -Force -Path (Split-Path $screenshotFixture) | Out-Null
$screenshotSpeech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $screenshotSpeech.SetOutputToWaveFile($screenshotFixture)
    $screenshotSpeech.Speak('Screenshot')
} finally {
    $screenshotSpeech.Dispose()
}

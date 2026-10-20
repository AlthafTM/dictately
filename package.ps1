param([switch]$Build)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Build) {
    & "$PSScriptRoot\build-native.ps1" build --release --locked
    Copy-Item -LiteralPath native\target\release\transcribr.exe -Destination Transcribr.exe -Force
}
if (-not (Test-Path -LiteralPath Transcribr.exe)) {throw 'Build Transcribr.exe first, or run package.ps1 -Build'}
$version = ((Get-Content native\Cargo.toml | Select-String '^version = "([^"]+)"').Matches[0].Groups[1].Value)
$stage = Join-Path $PSScriptRoot ('dist\stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage,"$stage\assets","$stage\licenses","$stage\docs" -Force | Out-Null
foreach ($name in @('Transcribr.exe','Start Transcribr.vbs','Stop Transcribr.ps1','setup.ps1','live_worker.py','engine.py','settings_window.py','download_turbo.py','requirements.txt','requirements-gpu.txt','README.md','THIRD_PARTY.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $stage
}
foreach ($name in @('transcribr.ico','transcribr.png','speech-model.json')) {
    Copy-Item -LiteralPath (Join-Path "$PSScriptRoot\assets" $name) -Destination "$stage\assets"
}
Copy-Item -LiteralPath "$PSScriptRoot\licenses\faster-whisper-LICENSE" -Destination "$stage\licenses"
Copy-Item -LiteralPath "$PSScriptRoot\docs\DEVELOPMENT.md" -Destination "$stage\docs"
Copy-Item -LiteralPath "$PSScriptRoot\docs\AI-MODELS.md" -Destination "$stage\docs"
foreach ($name in @('speech_models.py','select_model.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $stage
}
$archive = Join-Path $PSScriptRoot "dist\Transcribr-$version-windows-x64.zip"
Compress-Archive -Path "$stage\*" -DestinationPath $archive -Force
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
"$hash  $([IO.Path]::GetFileName($archive))" | Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
Write-Host $archive

param([switch]$Build)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Build) {
    & "$PSScriptRoot\build-native.ps1" build --release --locked
    Copy-Item -LiteralPath native\target\release\dictately.exe -Destination Dictately.exe -Force
}
if (-not (Test-Path -LiteralPath Dictately.exe)) {throw 'Build Dictately.exe first, or run package.ps1 -Build'}
$version = ((Get-Content native\Cargo.toml | Select-String '^version = "([^"]+)"').Matches[0].Groups[1].Value)
$stage = Join-Path $PSScriptRoot ('dist\stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage,"$stage\assets","$stage\licenses","$stage\docs" -Force | Out-Null
foreach ($name in (Get-Content "$PSScriptRoot\assets\package-files.json" -Raw | ConvertFrom-Json)) {
    $destination = Join-Path $stage $name
    New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $destination
}
$archive = Join-Path $PSScriptRoot "dist\Dictately-$version-windows-x64.zip"
Compress-Archive -Path "$stage\*" -DestinationPath $archive -Force
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
"$hash  $([IO.Path]::GetFileName($archive))" | Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
Write-Host $archive

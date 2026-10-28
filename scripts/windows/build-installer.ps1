param([switch]$SkipFreeze)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $taskRoot
$python = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/windows/setup.ps1 first to create the build environment' }
if (-not $SkipFreeze) {
    & $python -m pip install -r requirements/bootstrap.txt
    if ($LASTEXITCODE -ne 0) { throw 'Package installer security update failed' }
    & $python -m pip install -r requirements/installer.txt
    if ($LASTEXITCODE -ne 0) { throw 'Installer build dependencies failed' }
    & $python -m PyInstaller --noconfirm --distpath dist\frozen --workpath .tools\freeze-build installer\runtime.spec
    if ($LASTEXITCODE -ne 0) { throw 'Private runtime build failed' }
}
& "$taskRoot\scripts/windows/build-native.ps1" build --release --locked
if ($LASTEXITCODE -ne 0) { throw 'Native build failed' }
$version = ((Get-Content native\Cargo.toml | Select-String '^version = "([^"]+)"').Matches[0].Groups[1].Value)
$stage = Join-Path $taskRoot ('dist\installer-stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage,"$stage\runtime" -Force | Out-Null
$files = Get-Content assets\package-files.json -Raw | ConvertFrom-Json
foreach ($name in $files) {
    $destination = Join-Path $stage $name
    New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
    $source = if ($name -eq 'Dictately.exe') { 'native\target\release\dictately.exe' } else { $name }
    Copy-Item -LiteralPath $source -Destination $destination -Force
}
Copy-Item -LiteralPath dist\frozen\DictatelyWorker -Destination "$stage\runtime\worker" -Recurse
Copy-Item -LiteralPath dist\frozen\DictatelySettings -Destination "$stage\runtime\settings" -Recurse
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler is required' }
$compilerArgs = @('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',
    '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Core.dll',
    '/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll',
    '/win32manifest:installer\setup.manifest','/win32icon:assets\dictately.ico')
& $compiler @compilerArgs /define:UNINSTALLER "/out:$stage\Uninstall Dictately.exe" installer\Setup.cs
if ($LASTEXITCODE -ne 0) { throw 'Uninstaller compilation failed' }
& $python installer\make_payload.py $stage
if ($LASTEXITCODE -ne 0) { throw 'Payload creation failed' }
$payload = Join-Path $stage 'payload.zip'
$installer = Join-Path $taskRoot "dist\Dictately-$version-Setup.exe"
& $compiler @compilerArgs "/resource:$payload,Dictately.payload.zip" "/out:$installer" installer\Setup.cs
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLower()
"$hash  $([IO.Path]::GetFileName($installer))" | Set-Content -LiteralPath "$installer.sha256" -Encoding ascii
Write-Host "Installer: $installer"
Write-Host "Stage: $stage"

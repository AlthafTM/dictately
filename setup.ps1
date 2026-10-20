param(
    [switch]$Gpu,
    [switch]$Rebuild,
    [switch]$StartWithWindows
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Transcribr requires 64-bit Windows' }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 (64-bit), including the py launcher and Tcl/Tk' }
}
$requirements = if ($Gpu) { 'requirements-gpu.txt' } else { 'requirements.txt' }
& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
& $python download_turbo.py
if ($LASTEXITCODE -ne 0) { throw 'Model download or checksum verification failed' }
$executable = Join-Path $PSScriptRoot 'Transcribr.exe'
if ($Rebuild -or -not (Test-Path -LiteralPath $executable)) {
    & "$PSScriptRoot\build-native.ps1" build --release --locked
    Copy-Item -LiteralPath "$PSScriptRoot\native\target\release\transcribr.exe" -Destination $executable -Force
}
$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'Transcribr.lnk'
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.IconLocation = "$PSScriptRoot\assets\transcribr.ico"
$shortcut.Save()
$settingsShortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Transcribr Settings.lnk'))
$settingsShortcut.TargetPath = $executable
$settingsShortcut.Arguments = '--settings'
$settingsShortcut.WorkingDirectory = $PSScriptRoot
$settingsShortcut.IconLocation = "$PSScriptRoot\assets\transcribr.ico"
$settingsShortcut.Save()
if ($StartWithWindows) { Start-Process -FilePath $executable -ArgumentList '--enable-startup' -WindowStyle Hidden -Wait }
Write-Host 'Transcribr is ready. F12 toggles dictation and F11 opens the language picker by default. Use Transcribr Settings or the tray menu to change shortcuts.'

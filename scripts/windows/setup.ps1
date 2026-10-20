param(
    [switch]$Gpu,
    [switch]$Rebuild,
    [switch]$StartWithWindows
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $taskRoot
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Dictately requires 64-bit Windows' }
$python = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 (64-bit), including the py launcher and Tcl/Tk' }
}
$requirements = if ($Gpu) { 'requirements/gpu.txt' } else { 'requirements/base.txt' }
& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
& $python app/download_turbo.py
if ($LASTEXITCODE -ne 0) { throw 'Model download or checksum verification failed' }
$executable = Join-Path $taskRoot 'Dictately.exe'
if ($Rebuild -or -not (Test-Path -LiteralPath $executable)) {
    & "$taskRoot\scripts/windows/build-native.ps1" build --release --locked
    Copy-Item -LiteralPath "$taskRoot\native\target\release\dictately.exe" -Destination $executable -Force
}
$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'Dictately.lnk'
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = $taskRoot
$shortcut.IconLocation = "$taskRoot\assets\dictately.ico"
$shortcut.Save()
$settingsShortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Dictately Settings.lnk'))
$settingsShortcut.TargetPath = $executable
$settingsShortcut.Arguments = '--settings'
$settingsShortcut.WorkingDirectory = $taskRoot
$settingsShortcut.IconLocation = "$taskRoot\assets\dictately.ico"
$settingsShortcut.Save()
if (-not ('DictatelyIconRefresh' -as [type])) {
    Add-Type 'using System; using System.Runtime.InteropServices; public static class DictatelyIconRefresh { [DllImport("shell32.dll", CharSet=CharSet.Unicode)] public static extern void SHChangeNotify(uint events, uint flags, string item1, IntPtr item2); }'
}
foreach ($taskIconItem in @($shortcutPath, $settingsShortcut.FullName, "$taskRoot\assets\dictately.ico", $executable)) {
    [DictatelyIconRefresh]::SHChangeNotify(0x2000, 0x5, $taskIconItem, [IntPtr]::Zero)
}
if ($StartWithWindows) { Start-Process -FilePath $executable -ArgumentList '--enable-startup' -WindowStyle Hidden -Wait }
Write-Host 'Dictately is ready. F12 toggles dictation and F10 opens the language picker by default. Use Dictately Settings or the tray menu to change shortcuts.'

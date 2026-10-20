$ErrorActionPreference = 'Stop'
$taskStatusPath = Join-Path $env:LOCALAPPDATA 'Dictately\status.json'
$taskStatus = if (Test-Path -LiteralPath $taskStatusPath) { Get-Content -LiteralPath $taskStatusPath -Raw | ConvertFrom-Json } else { $null }
$taskExecutable = Join-Path $PSScriptRoot 'Dictately.exe'
$controllers = @(Get-CimInstance Win32_Process -Filter "Name='Dictately.exe'" | Where-Object { $_.ExecutablePath -eq $taskExecutable })
foreach ($controller in $controllers) {
    if ($taskStatus -and $controller.ProcessId -eq $taskStatus.pid) {
        if (-not ('DictatelyStop' -as [type])) {
            Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class DictatelyStop { [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l); }'
        }
        [void][DictatelyStop]::PostMessage([IntPtr]$taskStatus.hwnd, 0x8004, [IntPtr]::Zero, [IntPtr]::Zero)
        Wait-Process -Id $controller.ProcessId -Timeout 8 -ErrorAction SilentlyContinue
    }
    if (Get-Process -Id $controller.ProcessId -ErrorAction SilentlyContinue) { Stop-Process -Id $controller.ProcessId }
}

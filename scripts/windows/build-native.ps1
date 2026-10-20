$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$localCargo = Join-Path $taskRoot '.tools\cargo\bin\cargo.exe'
if (Test-Path -LiteralPath $localCargo) {
    $env:CARGO_HOME = Join-Path $taskRoot '.tools\cargo'
    $env:RUSTUP_HOME = Join-Path $taskRoot '.tools\rustup'
    $cargo = $localCargo
} else {
    $cargo = (Get-Command cargo -ErrorAction Stop).Source
}
$compiler = Join-Path $taskRoot '.tools\compiler\w64devkit\bin'
if (Test-Path -LiteralPath $compiler) { $env:PATH = $compiler + ';' + $env:PATH }
Push-Location (Join-Path $taskRoot 'native')
try {
    & $cargo @args
    if ($LASTEXITCODE -ne 0) { throw "Cargo exited with $LASTEXITCODE" }
} finally { Pop-Location }

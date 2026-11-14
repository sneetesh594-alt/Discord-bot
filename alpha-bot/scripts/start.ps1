$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        throw 'Run scripts\setup.ps1 first.'
    }
    & '.\.venv\Scripts\python.exe' -m aegis
    if ($LASTEXITCODE -ne 0) { throw "Bot exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}

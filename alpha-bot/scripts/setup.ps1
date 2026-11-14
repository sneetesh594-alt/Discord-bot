$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+ and add it to PATH.' }
    }
    & '.\.venv\Scripts\python.exe' -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        & '.\.venv\Scripts\python.exe' -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Could not install pip in the project virtual environment.' }
    }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & '.\.venv\Scripts\python.exe' -m pip install --no-deps .
    if ($LASTEXITCODE -ne 0) { throw 'Bot installation failed.' }
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    & '.\.venv\Scripts\python.exe' -m aegis --check
    if ($LASTEXITCODE -ne 0) { throw 'Configuration validation failed.' }
    Write-Host 'Ready. Edit .env with your own bot token, then run scripts\start.ps1.'
} finally {
    Pop-Location
}

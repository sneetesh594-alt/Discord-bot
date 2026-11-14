param([switch]$SyncCommands)
$ErrorActionPreference = 'Stop'
$botRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $botRoot
try {
    $pythonPath = Join-Path $botRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts\setup.ps1 first.' }
    if ($SyncCommands) { $env:SYNC_COMMANDS = 'true' }
    $restartDelay = 2
    while ($true) {
        & $pythonPath -m aegis
        $botExit = $LASTEXITCODE
        if ($botExit -eq 0) { break }
        if ($botExit -eq 2) { throw 'Bot configuration/token/instance lock requires attention.' }
        $healthPath = Join-Path $botRoot 'data\aegis.health.json'
        if (Test-Path -LiteralPath $healthPath) {
            $lastHealth = Get-Content -LiteralPath $healthPath -Raw | ConvertFrom-Json
            if ($lastHealth.commands_synced) { $env:SYNC_COMMANDS = 'false' }
        }
        Write-Warning "Bot exited ($botExit); retrying in $restartDelay seconds."
        Start-Sleep -Seconds $restartDelay
        $restartDelay = [Math]::Min(60, $restartDelay * 2)
    }
} finally {
    Pop-Location
}

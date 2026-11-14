param(
    [string]$EnvPath = (Join-Path (Split-Path -Parent $PSScriptRoot) '.env'),
    [securestring]$ClientSecret
)
$ErrorActionPreference = 'Stop'
Write-Host 'Alpha Discord OAuth setup - saves only to your local .env.'
Write-Host 'In the Alpha application OAuth2 page, register:'
Write-Host 'http://127.0.0.1:8765/auth/callback'
Write-Host 'Use the OAuth Client Secret, not the Discord bot token.'
if ($null -eq $ClientSecret) {
    $ClientSecret = Read-Host 'Paste OAuth Client Secret (input is hidden)' -AsSecureString
}
$secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ClientSecret)
try {
    $secretText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    if ([string]::IsNullOrWhiteSpace($secretText) -or $secretText.Length -lt 16 -or $secretText.Contains("`r") -or $secretText.Contains("`n")) {
        throw 'Secret is empty/too short or contains a newline. Nothing was saved.'
    }
    $localEnv = [IO.Path]::GetFullPath($EnvPath)
    if (-not (Test-Path -LiteralPath $localEnv)) {
        throw 'Local .env not found. Run scripts/setup.ps1 first.'
    }
    $text = [IO.File]::ReadAllText($localEnv)
    $quotedSecret = '"' + $secretText.Replace('\','\\').Replace('"','\"') + '"'
    $values = [ordered]@{
        DISCORD_CLIENT_SECRET = $quotedSecret
        DASHBOARD_ENABLED = 'true'
        DASHBOARD_PUBLIC_URL = 'http://127.0.0.1:8765'
    }
    foreach ($key in $values.Keys) {
        $line = $key + '=' + $values[$key]
        $pattern = '(?m)^' + [regex]::Escape($key) + '=.*$'
        if ([regex]::IsMatch($text,$pattern)) {
            $replacementLine = $line
            $text = [regex]::Replace($text,$pattern,[Text.RegularExpressions.MatchEvaluator]{param($m) $replacementLine})
        } else {
            $text = $text.TrimEnd() + "`r`n" + $line + "`r`n"
        }
    }
    $temporaryEnv = $localEnv + '.tmp'
    [IO.File]::WriteAllText($temporaryEnv,$text,(New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporaryEnv -Destination $localEnv -Force
    Write-Host 'Local OAuth settings saved. Secret was not printed or sent to GitHub.'
    Write-Host 'Stop the known Alpha instance, then run Start-Alpha.cmd and open the dashboard.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    $secretText = $null
    $text = $null
    $quotedSecret = $null
}

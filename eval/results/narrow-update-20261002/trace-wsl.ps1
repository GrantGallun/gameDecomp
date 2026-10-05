$ErrorActionPreference = 'Stop'
Start-Transcript -Path (Join-Path $PSScriptRoot 'wsl-trace.log') -Force | Out-Null
$taskTraceStarted = $false
try {
    & wpr.exe -start ((Join-Path $PSScriptRoot 'wsl-focused.wprp') + '!WSL') -filemode
    if ($LASTEXITCODE -ne 0) { throw 'Trace start failed; leaving existing sessions untouched.' }
    $taskTraceStarted = $true
    & (Join-Path $PSScriptRoot 'wsl-guest-diagnostic.ps1') -TaskMode ubuntu-admin
} catch { Write-Output $_.Exception.Message }
finally {
    if ($taskTraceStarted) {
        & wpr.exe -stop (Join-Path $PSScriptRoot 'wsl-launch.etl')
        & tracerpt.exe (Join-Path $PSScriptRoot 'wsl-launch.etl') -o (Join-Path $PSScriptRoot 'wsl-launch.xml') -of XML -y
    }
    Stop-Transcript | Out-Null
}

$ErrorActionPreference = 'Stop'
Start-Transcript -Path (Join-Path $PSScriptRoot 'wsl-service-start.log') -Force | Out-Null
try {
    Get-Process -Name vmmem,VmmemWSL,vmwp -ErrorAction SilentlyContinue | Select-Object ProcessName,Id
    Start-Service -Name WslService -ErrorAction Stop
    Get-CimInstance Win32_Service -Filter "Name='WslService'" | Select-Object Name,State,ProcessId | ConvertTo-Json
} finally { Stop-Transcript | Out-Null }

$ErrorActionPreference = 'Stop'
$taskDiagnosticLog = Join-Path $PSScriptRoot 'wsl-vm-diagnostics.log'
Start-Transcript -Path $taskDiagnosticLog -Force | Out-Null
try {
    & hcsdiag.exe list
    Write-Output ('hcsdiag exit: ' + $LASTEXITCODE)
    Get-CimInstance Win32_Service -Filter "Name='WslService' OR Name='vmcompute' OR Name='hns'" |
        Select-Object Name,State,ProcessId | ConvertTo-Json
    Get-CimInstance Win32_Process -Filter "Name='vmmem' OR Name='vmwp.exe'" |
        Select-Object Name,ProcessId,CreationDate,KernelModeTime,UserModeTime,WorkingSetSize | ConvertTo-Json
    Get-WinEvent -ListLog '*Lxss*','*Hyper-V-Compute*','*Hyper-V-Worker*' -ErrorAction SilentlyContinue |
        Select-Object LogName,RecordCount | ConvertTo-Json
    foreach ($taskLogName in @('Microsoft-Windows-Hyper-V-Compute-Admin', 'Microsoft-Windows-Hyper-V-Compute-Operational', 'Microsoft-Windows-Hyper-V-Worker-Admin')) {
        Write-Output $taskLogName
        Get-WinEvent -FilterHashtable @{ LogName = $taskLogName; StartTime = (Get-Date).AddMinutes(-30) } -MaxEvents 12 -ErrorAction SilentlyContinue |
            Select-Object TimeCreated,Id,LevelDisplayName,Message | ConvertTo-Json -Depth 3
    }
} catch {
    Write-Output $_.Exception.Message
} finally {
    Stop-Transcript | Out-Null
}

$ErrorActionPreference = 'Stop'
Start-Transcript -Path (Join-Path $PSScriptRoot 'wsl-vm-reset.log') -Force | Out-Null
try {
    $taskVmId = '66F7E1EC-E261-4292-9640-A60771A4C4B7'
    $taskInventory = (& hcsdiag.exe list | Out-String)
    Write-Output $taskInventory
    if ($LASTEXITCODE -ne 0 -or $taskInventory -notmatch ($taskVmId + ', WSL')) { throw 'Expected WSL VM is no longer present; refusing reset.' }
    $taskVmRows = @($taskInventory -split "`n" | Where-Object { $_ -match '^\s+VM,' })
    if ($taskVmRows.Count -ne 1) { throw 'Other VMs exist; refusing reset.' }
    $taskService = Get-CimInstance Win32_Service -Filter "Name='WslService'"
    $taskDaemon = Get-Process -Id $taskService.ProcessId -ErrorAction Stop
    if ($taskDaemon.ProcessName -ne 'wslservice') { throw 'Service PID identity mismatch.' }
    Write-Output ('Stopping WSL service daemon PID ' + $taskDaemon.Id)
    Stop-Process -Id $taskDaemon.Id -Force -ErrorAction Stop
    $taskKill = Start-Process hcsdiag.exe -ArgumentList @('kill',$taskVmId) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'wsl-vm-kill.stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'wsl-vm-kill.stderr.log')
    $taskKillHandle = $taskKill.Handle
    if (-not $taskKill.WaitForExit(15000)) { $taskKill.Kill(); throw 'Targeted VM termination timed out.' }
    $taskKill.Refresh()
    Write-Output ('hcsdiag kill exit: ' + $taskKill.ExitCode)
    $taskRemaining = (& hcsdiag.exe list | Out-String)
    Write-Output $taskRemaining
    if ($LASTEXITCODE -ne 0 -or $taskRemaining -match $taskVmId) { throw 'Targeted VM is still present.' }
    Start-Service -Name WslService -ErrorAction Stop
    Get-CimInstance Win32_Service -Filter "Name='WslService'" | Select-Object Name,State,ProcessId | ConvertTo-Json
} catch {
    Write-Output ('RESET ERROR: ' + $_.Exception.Message)
} finally {
    Stop-Transcript | Out-Null
}

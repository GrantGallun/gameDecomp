# Recover only the hung WSL service. Never terminate an active WSL VM.
$ErrorActionPreference = 'Stop'
$taskRecoveryDir = $PSScriptRoot
$taskRecoveryLog = Join-Path $taskRecoveryDir 'wsl-recovery.log'
$taskRecoveryResult = Join-Path $taskRecoveryDir 'wsl-recovery.json'
$taskRecoveryState = [ordered]@{ status = 'started'; started = (Get-Date).ToString('o'); error = $null }
try {
    Start-Transcript -Path $taskRecoveryLog -Force | Out-Null
    $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new($taskIdentity)
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Windows administrator approval is required to control WslService.'
    }
    $taskService = Get-CimInstance Win32_Service -Filter "Name='WslService'"
    $taskRecoveryState.before = $taskService.State
    if ($taskService.State -eq 'Stop Pending') {
        $taskVm = @(Get-Process -Name VmmemWSL,vmmem -ErrorAction SilentlyContinue)
        if ($taskVm.Count -ne 0) {
            throw 'An active virtual machine was found; refusing forced service recovery.'
        }
        $taskDaemon = Get-Process -Id $taskService.ProcessId -ErrorAction Stop
        if ($taskDaemon.ProcessName -ne 'wslservice') {
            throw 'Service process identity changed; refusing termination.'
        }
        Write-Output ('Resetting hung WSL service process ' + $taskDaemon.Id + '; no VM is running.')
        Stop-Process -Id $taskDaemon.Id -Force -ErrorAction Stop
        $taskRecoveryState.hung_service_reset = $true
    }
    Start-Service -Name WslService -ErrorAction Stop
    $taskRecoveryState.after = (Get-Service -Name WslService).Status.ToString()
    if ($taskRecoveryState.after -ne 'Running') {
        throw 'WSL service did not reach Running.'
    }
    $taskRecoveryState.status = 'service_running'
} catch {
    $taskRecoveryState.status = 'failed'
    $taskRecoveryState.error = $_.Exception.Message
    Write-Output $taskRecoveryState.error
} finally {
    $taskRecoveryState.finished = (Get-Date).ToString('o')
    [IO.File]::WriteAllText($taskRecoveryResult, ($taskRecoveryState | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}

param([string]$TaskMode = 'guest')
$ErrorActionPreference = 'Stop'
$taskPrefix = Join-Path $PSScriptRoot ('wsl-' + $TaskMode)
$taskProcessInfo = New-Object System.Diagnostics.ProcessStartInfo
$taskProcessInfo.FileName = 'C:\Windows\System32\wsl.exe'
$taskProcessInfo.Arguments = if ($TaskMode -eq 'ubuntu-admin') { '-d Ubuntu --exec /bin/echo WSL_READY' } else { '--debug-shell' }
$taskProcessInfo.UseShellExecute = $false
$taskProcessInfo.CreateNoWindow = $true
$taskProcessInfo.RedirectStandardInput = $true
$taskProcessInfo.RedirectStandardOutput = $true
$taskProcessInfo.RedirectStandardError = $true
$taskProcess = New-Object System.Diagnostics.Process
$taskProcess.StartInfo = $taskProcessInfo
$taskProcess.Start() | Out-Null
$taskOutput = $taskProcess.StandardOutput.ReadToEndAsync()
$taskError = $taskProcess.StandardError.ReadToEndAsync()
if ($TaskMode -eq 'guest') {
    $taskProcess.StandardInput.WriteLine('dmesg | tail -100; ps -ef; exit')
} elseif ($TaskMode -eq 'guest-stacks') {
    $taskProcess.StandardInput.WriteLine('for p in 1 $(pidof gns); do echo PROCESS=$p; for t in /proc/$p/task/*; do echo TASK=$t; cat $t/wchan $t/stack; done; done; mount; exit')
}
$taskProcess.StandardInput.Close()
$taskFinished = $taskProcess.WaitForExit(15000)
if (-not $taskFinished) { $taskProcess.Kill(); $taskProcess.WaitForExit() }
[IO.File]::WriteAllText(($taskPrefix + '.stdout.log'), $taskOutput.Result)
[IO.File]::WriteAllText(($taskPrefix + '.stderr.log'), $taskError.Result)
@{ finished = $taskFinished; exit_code = $taskProcess.ExitCode; timestamp = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath ($taskPrefix + '.json')

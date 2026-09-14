param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$url = "http://127.0.0.1:$Port"
try { $existing = Invoke-RestMethod "$url/api/status" -TimeoutSec 2 } catch { $existing = $null }
if ($existing.app -ne 'gameDecomp-progress') {
    $pythonExe = (Get-Command python).Source
    $logs = Join-Path $projectRoot 'eval/results/progress-app'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $process = Start-Process -FilePath $pythonExe -ArgumentList @('-m', 'eval.progress_app', '--port', $Port) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'server.log') -RedirectStandardError (Join-Path $logs 'server-error.log') -PassThru
    $process.Id | Set-Content (Join-Path $logs 'server.pid')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try { $ready = Invoke-RestMethod "$url/api/status" -TimeoutSec 2; if ($ready.app -eq 'gameDecomp-progress') { break } } catch { }
        Start-Sleep -Milliseconds 200
    }
    if ($ready.app -ne 'gameDecomp-progress') { throw "Dashboard did not start. See $logs/server-error.log" }
}
Start-Process $url
Write-Output "Progress dashboard: $url"

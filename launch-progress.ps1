param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$url = "http://127.0.0.1:$Port"
try { $existing = Invoke-RestMethod "$url/api/health" -TimeoutSec 2 } catch { $existing = $null }
if ($existing.app -ne 'gameDecomp-progress') {
    $logs = Join-Path $projectRoot 'eval/results/progress-app'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    # Served from WSL. The live checkpoint and its SQLite store are WSL-side (launch.json --state since the
    # 2026-09-20 relocation) and SQLite cannot lock over \\wsl.localhost; WSL forwards 127.0.0.1 to Windows.
    $projectLinux = '/mnt/' + $projectRoot.Substring(0,1).ToLower() + ($projectRoot.Substring(2) -replace '\\','/')
    $process = Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d', 'Ubuntu', '--cd', $projectLinux, '-e', '/home/grant/decomp/sbk1/.venv/bin/python', '-m', 'eval.progress_app', '--port', $Port) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'server.log') -RedirectStandardError (Join-Path $logs 'server-error.log') -PassThru
    $process.Id | Set-Content (Join-Path $logs 'server.pid')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try { $ready = Invoke-RestMethod "$url/api/health" -TimeoutSec 2; if ($ready.app -eq 'gameDecomp-progress') { break } } catch { }
        Start-Sleep -Milliseconds 200
    }
    if ($ready.app -ne 'gameDecomp-progress') { throw "Dashboard did not start. See $logs/server-error.log" }
}
Start-Process $url
Write-Output "Progress dashboard: $url"

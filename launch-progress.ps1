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
    # A cold WSL start takes several seconds before Python even runs, so allow a minute, but stop
    # waiting as soon as wsl.exe exits: then the server is not coming.
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        try { $ready = Invoke-RestMethod "$url/api/health" -TimeoutSec 2; if ($ready.app -eq 'gameDecomp-progress') { break } } catch { }
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($ready.app -ne 'gameDecomp-progress') {
        # wsl.exe writes its own errors to stdout, in UTF-16, so show both logs with the NULs removed.
        $detail = foreach ($name in 'server-error.log', 'server.log') {
            $text = (Get-Content -Raw -ErrorAction SilentlyContinue (Join-Path $logs $name)) -replace "`0", ''
            if ($text -and $text.Trim()) { "--- ${name}:`n" + $text.Trim() }
        }
        $hint = if ("$detail" -match 'ERROR_NO_SYSTEM_RESOURCES') {
            "`nWSL could not start because Windows is short of memory. Close something large and retry " +
            "(docs/RUNNING.md, Troubleshooting)."
        } else { '' }
        throw ("Dashboard did not start.`n" + ($detail -join "`n") + $hint)
    }
}
Start-Process $url
Write-Output "Progress dashboard: $url"

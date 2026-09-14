param([int]$Port = 11435, [ValidateSet(1,2)][int]$Parallel = 1,
      [ValidateSet('f16','q8_0')][string]$CacheType = 'f16')
$ErrorActionPreference = 'Stop'
$logs = Join-Path $PSScriptRoot 'eval/results/gpu-pipeline-20260911'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$existing = $null
try { $existing = Invoke-RestMethod "http://127.0.0.1:$Port/api/version" -TimeoutSec 2 } catch { }
if ($existing) { throw "Port $Port is already serving Ollama; reuse it or stop that dedicated server before changing settings." }
$ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'
# Process-local settings: leave the desktop Ollama app and user environment intact.
$settings = @{
    OLLAMA_HOST = "0.0.0.0:$Port"
    OLLAMA_FLASH_ATTENTION = '1'
    OLLAMA_KV_CACHE_TYPE = $CacheType
    OLLAMA_NUM_PARALLEL = "$Parallel"
    OLLAMA_MAX_LOADED_MODELS = '1'
    OLLAMA_CONTEXT_LENGTH = '32768'
    OLLAMA_KEEP_ALIVE = '30m'
}
$saved = @{}
try {
    foreach ($key in $settings.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $settings[$key], 'Process')
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $process = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "$stamp-server.log") -RedirectStandardError (Join-Path $logs "$stamp-server-error.log")
    @{ pid=$process.Id; port=$Port; settings=$settings; executable=$ollamaExe; started_at=$stamp } | ConvertTo-Json | Set-Content (Join-Path $logs 'server.json')
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { $ready=Invoke-RestMethod "http://127.0.0.1:$Port/api/version" -TimeoutSec 2; break } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $ready) { throw 'Dedicated campaign model server did not become ready; inspect the GPU experiment logs.' }
    Write-Output "Campaign Ollama ready on port $Port, $Parallel inference slots, $CacheType cache (PID $($process.Id))."
} finally {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
}

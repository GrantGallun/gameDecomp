# Model loads and request outcomes in the Ollama server log after a given line number.
#   powershell -File measure.ps1 -After 22615
param([int]$After = 0)
$l = "$env:LOCALAPPDATA\Ollama\server.log"
$lines = Get-Content $l | Select-Object -Skip $After
"lines examined: $($lines.Count)"
"model loads by n_ctx:"
$lines | Select-String -Pattern 'llama_context: n_ctx\s+=' | ForEach-Object { ($_.Line -replace '.*n_ctx\s+=\s*', '').Trim() } |
    Group-Object | Select-Object Name, Count | Format-Table -AutoSize
$req = $lines | Select-String -Pattern '\[GIN\].*POST\s+"/api/(generate|chat)"'
"requests: $($req.Count)"
$req | ForEach-Object { ($_.Line -split '\|')[1].Trim() } | Group-Object | Select-Object Name, Count | Format-Table -AutoSize
$d = $req | ForEach-Object { $t = ($_.Line -split '\|')[2].Trim()
    if ($t -match '(\d+)m([\d.]+)s') { [double]$matches[1] * 60 + [double]$matches[2] }
    elseif ($t -match '([\d.]+)s') { [double]$matches[1] } else { 0 } }
if ($d) { "median request seconds: {0:N0}" -f (($d | Sort-Object)[[int]($d.Count / 2)]) }

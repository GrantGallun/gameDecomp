param([switch]$NoRepair)
$ErrorActionPreference = 'Stop'
$projectRoot = 'C:\Code\gameDecomp'
$runRoot = Join-Path $projectRoot 'eval\results\resume-pipeline-20260908'
$monitorRoot = Join-Path $runRoot 'monitor'
New-Item -ItemType Directory -Path $monitorRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runLinux = '/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908'
$pythonLinux = '/home/grant/decomp/sbk1/.venv/bin/python'
$serviceLinux = '/mnt/c/Code/gameDecomp/eval/campaign_service.py'
# Bound the workers' fast_runtime caches (2026-09-22: one reached 141 GB and C: filled). Eviction removes
# only cached values, least recently used first, and skips entries a worker holds, so it is safe while
# the campaign runs; a miss just recompiles. A failure here is logged and never blocks the health check.
try {
    $launch = Get-Content -LiteralPath (Join-Path $runRoot 'launch.json') -Raw | ConvertFrom-Json
    $workerRoot = $launch.command[[array]::IndexOf($launch.command, '--worker-root') + 1]
    $prune = & wsl.exe -d Ubuntu --cd '/mnt/c/Code/gameDecomp' -e bash -c "$pythonLinux -m eval.cache_prune $workerRoot/*/cache --budget-gb 4 --apply" 2>&1
    ($prune -join "`n") | Set-Content -LiteralPath (Join-Path $monitorRoot "$stamp-cache-prune.jsonl") -Encoding UTF8
} catch {
    "cache prune failed: $_" | Set-Content -LiteralPath (Join-Path $monitorRoot "$stamp-cache-prune.jsonl") -Encoding UTF8
}
# Pack source attribution in attempts the campaign wrote since the last run (lossless, byte-checked,
# reversible with --expand; see eval/attempt_compact.py). No vacuum here: freed pages are reused.
try {
    $dbLinux = $launch.command[[array]::IndexOf($launch.command, '--db') + 1]
    $compact = & wsl.exe -d Ubuntu --cd '/mnt/c/Code/gameDecomp' -e $pythonLinux -m eval.attempt_compact $dbLinux --apply 2>&1
    ($compact -join "`n") | Set-Content -LiteralPath (Join-Path $monitorRoot "$stamp-attempt-compact.json") -Encoding UTF8
} catch {
    "attempt compaction failed: $_" | Set-Content -LiteralPath (Join-Path $monitorRoot "$stamp-attempt-compact.json") -Encoding UTF8
}
# Windows never reads the live campaign checkpoint: WSL performs the read.
if (Test-Path -LiteralPath (Join-Path $runRoot 'service.pause')) {
    $health = [pscustomobject]@{ status = 'paused'; reason = 'Durable pause marker present' }
} else {
    $raw = & wsl.exe -d Ubuntu -e $pythonLinux $serviceLinux --run $runLinux ensure
    if ($LASTEXITCODE -ne 0) {
        $health = [pscustomobject]@{ status = 'needs_repair'; error = 'WSL health command failed'; details = ($raw -join "`n") }
    } else {
        $health = ($raw -join "`n") | ConvertFrom-Json
    }
}
$health | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $monitorRoot "$stamp-health.json") -Encoding UTF8
if ($health.status -notin @('needs_repair','needs_review') -or $NoRepair) { return }
$prompt = @'
Perform the authorized hourly maintenance of C:\Code\gameDecomp's campaign.
Read eval/results/resume-pipeline-20260908/OPERATIONS.md first, then health.json
and service.json. The user authorized diagnosing and fixing campaign failures,
focused tests, recording explicit runtime amendments and resuming this same run.
Keep work within this project and its existing campaign. Preserve existing dirty
changes and all accepted candidates, receipts, model settings and correctness gates.
Never convert an emulator/harness failure into a semantic pass. Never change
test inputs or reduce test budgets to hide a failure. No unrelated refactors,
commits, pushes, messages, installation, WSL shutdown or permission bypasses.
If service.pause exists or service-control.json says paused, report paused and stop.
Use WSL for live checkpoint reads; Windows readers can block atomic replacement.
If healthy or complete, report that and stop. For a persistent failure, diagnose,
make the smallest defensible fix, run focused tests, and preserve the pre-change
checkpoint/code plus before/after hashes before any stopped-run snapshot amendment.
Only deploy an amendment when all other frozen pins verify and no worker holds
campaign.lock. Resume using eval/campaign_service.py --run <run> resume through WSL.
If permissions prevent validation or resumption, or correctness is uncertain,
leave needs_repair in place and report the concrete blocker. Do not bypass sandbox
or approval rules. Do not repeatedly rerun identical failing work.
Write a concise report of cause, changes, tests, remaining limitations and run status.
'@
$report = Join-Path $monitorRoot "$stamp-repair.md"
$events = Join-Path $monitorRoot "$stamp-codex.jsonl"
$errors = Join-Path $monitorRoot "$stamp-codex.stderr.log"
$codexCli = Get-ChildItem -LiteralPath 'C:\Users\grant\AppData\Local\OpenAI\Codex\bin' -Filter codex.exe -Recurse |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $codexCli) { throw 'No current bundled Codex CLI is available.' }
$prompt | & $codexCli exec --cd $projectRoot --sandbox workspace-write --json --output-last-message $report - 1> $events 2> $errors
if ($LASTEXITCODE -ne 0) { throw "Codex maintenance failed; see $errors" }

# Current campaign performance runtime

## Further reuse and controller improvements

Revision `20260911-throughput-reuse` adds incremental run-row payload copying,
single-open full file hashing, same-job compiler artifact naming, and complete
input-bound target exploration/stress caching. Candidate gates and historical
lineage remain intact. Worker state is scoped and restored between jobs; the
current launch allows eight jobs per process and the service resumes with
`--batch 200`. Three workers still feed one GPU inference slot.

Passing semantic receipts are compacted for prompts with their full report hash,
execution debt and contracts retained. The client rejects estimated context
overflow explicitly; it does not claim exact tokenization or truncate source.
The dashboard now exposes controller sub-stages, target-work cache hits and a
live score-improvement rate. Rates include current controller startup and elapsed
session time; service downtime remains excluded.

Measured metadata synchronization: 1.22–1.33s to 0.047–0.053s on the isolated
19.44 MB run table. Complete pin verification: 1.68–1.70s to 1.06–1.08s. Three
identical real compilations: 0.930s to 0.468s with every acceptance gate rerun.
These are component timings, not a whole-game speedup. Final staged suite:
2,047 passed; four real jobs in one process retain full cold/warm verdicts.
See [implementation and experiments](eval/results/optimization-implementation-20260911/README.md)
for receipts, policy decisions and rollback details. This section supersedes
older batch-size, per-job-process and checkpoint-rate descriptions below.

## Stable context: subsequent throughput correction

The live campaign now uses fixed 32k context on every model request. Ollama
reloaded the entire model whenever adaptive context switched between 16k and
32k, adding about 32 seconds of startup plus teardown delay. The dedicated
server already has room for one 32k f16 cache. Keeping that allocation avoids
churn and retains prompt-cache opportunities without changing output budgets.
The shared client's adaptive default remains available for other callers.

A four-request paired replay fell from 203.6s to 8.2s; both arms produced four
compiling/frontend-passing candidates, with differing generations and one lower
byte score in the fixed arm. This does not establish a whole-campaign multiplier.
Full results and rollback details: [stable-context experiment](eval/results/context-throughput-20260911/README.md).
Per-request timings and token counts are retained in worker performance receipts
and aggregated under `model_load_seconds`, `model_prompt_seconds`,
`model_generation_seconds`, `model_prompt_tokens`, `model_generated_tokens`.

## GPU update: current configuration

The live campaign now uses three private workers with rolling dispatch, replacing
the two-function waves documented below. Two model-preparation jobs feed one GPU
inference slot; the third favors CPU work. Imports and refills happen as each job
finishes. Original evidence-v1 priorities apply within each resource class.
Batch size is 50 to amortize startup; pause still drains current work before any
new dispatch. The dedicated Ollama endpoint is port 11435 with one loaded model,
f16 KV, Flash Attention and the existing adaptive context up to 32k.

Measured q8 KV saved 368 MiB per 32k slot but two q8 requests tied one f16 request
on equal-token throughput (31.9 vs 32.1 seconds). It was not retained. Two f16
slots caused severe memory pressure. Detailed benchmark, proposal-validation and
rollback receipts are in [the GPU experiment](eval/results/gpu-pipeline-20260911/README.md).
Launch the dedicated server with `powershell.exe -NoProfile -ExecutionPolicy Bypass
-File launch-campaign-gpu.ps1`; do not load a duplicate model on the old 11434
endpoint while it runs. All settings are process-local. The dashboard samples
GPU activity, allocated VRAM and power every five seconds off the HTTP thread.

## Local progress dashboard

Double-click `launch-progress.cmd` to start/reopen the dashboard at
`http://127.0.0.1:8765`. It runs as a hidden local Windows Python process and
refreshes every two seconds while its browser tab is visible. Closing the tab
does not stop the campaign. The launcher reuses an existing dashboard server.
Server logs and its PID are in `eval/results/progress-app`.

The view shows exact-match coverage, in-flight functions, recent results,
performance counters and cache hits. Search filters the recent results only.
“Pause live view” freezes this browser view, not the running campaign. Status
uses the supervisor heartbeat rather than the checkpoint's budget-stop label.
The backend reads only the compact pointer and a bounded log tail; it never
loads the full state database or changes campaign files. Backend status/tail
tests passed, and the rendered dashboard, live refresh, search and view pause
were verified in Chrome.

The September 11 runtime amendment enables `eval.fast_campaign` on the existing
`resume-pipeline-20260908` campaign. Its original model, model-call/output limits,
semantic budgets, compiler/frontend checks and exactness gates remain in use.
The amendment and complete prior checkpoint/launch configuration are in
`eval/results/resume-pipeline-20260908/revisions/20260911-incremental-parallel`.

## Persistence and recovery

`campaign.json` is now a small commit pointer. Its sibling `campaign.state.sqlite`
contains immutable, checksummed, compressed objects and append-only snapshot
manifests. Each manifest references every node, so every commit is a complete
logical snapshot even though only changed nodes are written. SQLite commits the
objects before the pointer is atomically replaced. An interrupted pointer write
leaves the previous snapshot usable. Object/commit corruption is rejected.
The service's previous-checkpoint pointer references the same retained store.

Readers needing full node details must use `eval.campaign_state.read(path)`.
To export a new legacy-format JSON snapshot:

```sh
python -m eval.campaign_state eval/results/resume-pipeline-20260908/campaign.json \
  --export /path/to/new-full-checkpoint.json
```

The service understands the new format and uses its small health summary for
polls. The controller validates and loads the complete state before resuming.
Keep the object store with the checkpoint; copying only the pointer is not a
self-contained backup. Old ad hoc scripts that directly parse `nodes` from
`campaign.json` must use the reader or an exported snapshot.

## Parallel work and cache scope

The controller dispatches two distinct functions using the existing evidence-v1
priorities and profiles at each wave boundary. This is an explicit scheduling
change: the second function can run before a new revisit of the first function.
Workers have separate native WSL workspaces and full private database histories.
Only append-only attempt/proposal/run/edge rows can be written by workers.
The controller imports them transactionally, remaps colliding IDs, and records
the mapping with an idempotent import receipt in the authoritative database.
Source/evidence identities and frozen inputs are checked before acceptance.

A completed private receipt can be replayed after interruption without another
model call. A process lease prevents a resumed worker duplicating a surviving
worker's job. As with the old runner, interruption before a durable result can
repeat model calls; exactly-once inference is not claimed. Import itself is
idempotent. Pause takes effect at a wave boundary after dispatched work settles.

Workers share one model-request lock. Compiler and semantic work can proceed
while the other worker waits for or uses the model. The worker directories and
caches live under `/home/grant/decomp/campaign-workers-20260911`, surviving WSL
restarts. Initial copies preserve all database history; later synchronization
moves only appended trajectory rows. This runtime does not perform integration.

Cache namespaces include the complete controller-verified input pin identity.
Header-layout reuse additionally binds includes, supplied types, compiler target,
requested globals, and repository identity, then issues a receipt for the current
source. Semantic reuse binds source, object, normalized assembly and the complete
panel identity. Compiler reuse requires the same build invocation, workspace,
source, recipe/scripts, prelude and target inputs. It restores successful build
artifacts; the ordinary scoring path still runs frontend/certificate checks.
Compiler cache hits have not yet been demonstrated on the real smoke cases.
No model-response cache or reduced semantic acceptance panel is enabled.

## Validation and measurement

Release suite: 2,144 WSL tests passed in 46.05 seconds. New tests cover complete
controller commits/resume, two-worker ID collisions, import rollback/idempotency,
source/evidence staleness, cache invalidation and corrupt/interrupted checkpoint
handling. A real two-worker, zero-model MIPS smoke compiled both saved functions
and imported their lineage into an isolated database. Warm replay produced the
same candidate hashes/scores/verdicts, with four layout and six semantic hits.

Single checkpoint measurement: 0.081 seconds for one changed node versus the
earlier 6.18-second full JSON write. Initial migration still costs several seconds;
loading full history measured about 7–10 seconds. These are component timings,
not a whole-campaign speedup. Cold/warm smoke timings are cache checks, not a
controlled estimate of parallel scaling or decompilation yield.

The checkpoint health summary and per-item receipts now expose queue, model,
compiler, layout, semantic, checkpoint and controller timings, cache hit/miss
counts, completed items, byte-score improvements and exact results. Rates use
active controller session time; service downtime and startup are not included.
Raw evidence is in `eval/results/campaign-speed-20260911`.

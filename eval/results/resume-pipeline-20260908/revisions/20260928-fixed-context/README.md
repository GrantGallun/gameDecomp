# Runtime option: pin the model context (`SOLVER_FIXED_CONTEXT=32768`)

Approved by the owner 2026-09-28. Runtime option only: no code, pin, candidate, budget, model, held-out set
or ledger row changes. `solver/prompt_budget.fixed_context` (present in the frozen code) reads the variable.

## Why

`prompt_budget.context_budget` sizes each request's `num_ctx` to the smallest power of two that fits the
prompt, capped at 32,768. Ollama rebuilds the whole model whenever `num_ctx` changes
(`CAMPAIGN_PERFORMANCE.md`: ~32 s per switch). With the campaign and experiment clients mixing prompt sizes,
the server thrashed. Measured from `%LOCALAPPDATA%\Ollama\server.log`, 12:20–14:25 on 2026-09-28:

| | |
|---|---:|
| model loads (`llama_context: n_ctx`) | 60 (42 at 98,304 = 3 × 32K, 18 at 49,152 = 3 × 16K) |
| generate/chat requests | 109: 99 ok, 7 client gave up (499), 3 server error (500) |
| median request duration | 162 s |

## What changes and what does not

Pinning at 32,768 keeps the adaptive policy's ceiling, so every prompt that fit before still fits, and
anything above 32K is refused exactly as before. Small prompts now use a 32K allocation instead of 16K.
VRAM with 3 slots at 32K was already the working set (15.6 of 16.3 GB, measured). Budget receipts record
`fixed_context: 32768`, so the setting is visible in every request's receipt. Seeded-generation cache keys
include `num_ctx`, so previously cached small-prompt generations will miss once.

## How it is applied

- Windows user variables `SOLVER_FIXED_CONTEXT=32768` and `WSLENV=SOLVER_FIXED_CONTEXT/u`, so every
  `wsl.exe` launch (including `GameDecomp-Hourly-Campaign-Maintenance`'s `ensure`) forwards it.
- The running supervisor was paused, drained, and resumed with the variable set, because a running process
  cannot acquire a new environment variable.

To revert: remove both user variables, then pause, drain and resume the campaign.

## After

Measured by `measure.ps1` (loads by `n_ctx` and request outcomes after a given log line). Result recorded
below once the campaign has run with the pin.

## Machine throttle, 2026-09-29 (owner request: the PC lagged badly)

Runtime environment only; no code, pin, budget or model change. Measured before: VRAM 15.8 of 16.3 GB (model
12.2 GB + desktop apps), GPU 91-100% busy, host RAM ~4.3 GB free, two NVIDIA driver errors (event 153, 23:53 and
00:03), WSL command bridge timing out.

- `OLLAMA_NUM_PARALLEL=1` (was 3): one generation stream.
- `SOLVER_NUM_GPU=20` (`solver/llm._throttle`, present in the frozen code): 20 of 25 layers on the GPU. Measured
  after: 25%/75% CPU/GPU split, VRAM 14.3 GB (about 2 GB free), GPU ~28% busy. Model calls are slower.
- `SOLVER_GAP_MS=2000`: two idle seconds after each model call.
- Both forwarded by `WSLENV=SOLVER_FIXED_CONTEXT/u:SOLVER_NUM_GPU/u:SOLVER_GAP_MS/u` (Windows user variables), so
  hourly restarts keep them; the resumed supervisor and worker were checked in /proc.
- `.wslconfig`: `[experimental] autoMemoryReclaim=gradual` (backup `.wslconfig.before-20260929`); applied by a
  WSL restart after draining. Host RAM free went from ~4.3 to 6.4 GB immediately after.
- `OLLAMA_GPU_OVERHEAD` was tried and removed: this Ollama version (0.34.4) planned the full model onto the GPU
  regardless.

The llama-server runner was set to BelowNormal CPU priority; that does not persist across Ollama restarts.
Throttling is a property of the machine, not of an experiment; per `_throttle`'s docstring it is never written into
a run configuration. To undo: remove SOLVER_NUM_GPU / SOLVER_GAP_MS (and set OLLAMA_NUM_PARALLEL=3), then restart
Ollama and pause-drain-resume the campaign.

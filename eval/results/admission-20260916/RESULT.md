# The admission bucket: 199 functions that have never compiled once

Measured 2026-09-16 against `kb-sbk1.sqlite`. Scripts: `.cache/admission/probe{,2,3,4}.py`.

## The population

| | |
|---|---|
| functions attempted at least once | 502 |
| ever compiled | 303 |
| **never compiled** | **199** |
| attempts spent on them | 3,232 |

**They are stale, and that is the headline.** Every attempt in this population falls between
2026-08-27 and 2026-09-06. **0 of the 199 have been attempted since 2026-09-07** — ten days. The
pipeline has changed since: `solver/compile_chain.py` (09-15), `solver/llm.py` (09-11).

They are also not under-explored:

| attempts | functions |
|---|---|
| 1 | 3 |
| 2–4 | 23 |
| 5–19 | 106 |
| **20+** | **67** |

173 of 199 have ≥5 attempts. This is not a population that has been ignored; it is one that was
worked hard and then frozen.

## Failure kinds

Counted as "function shows at least one failure of this kind", so these overlap:

| kind | functions |
|---|---|
| syntax error | 127 |
| do-while ban | 55 |
| undefined identifier | 44 |
| no text symbols | 36 |
| selector requires struct/union | 26 |
| redeclaration | 7 |

**Correction to an earlier guess of mine.** I expected the do-while ban to be the big clean win, on
the grounds that `rewrite_do_while` already exists. It is not. Restricting to functions where
*every* failed attempt shows only one kind:

| clean class | functions | attempts |
|---|---|---|
| syntax only | **63** | 712 |
| no text symbols only | **33** | 336 |
| do-while only | 2 | 4 |

The do-while ban appears in 55 functions but almost always alongside syntax errors, so a lowering
alone does not admit them. **The largest clean bucket is plain C syntax failures: 63 functions.**

## The finite work list: 193 undefined symbols

1,196 occurrences across 44 functions. This is the most tractable item in the bucket because it is
enumerable, and each entry is a declaration that is simply absent:

| symbol | occurrences |
|---|---|
| `sp` | 78 |
| `D_801124A0` | 78 |
| `CallbackTask` | 43 |
| `displayListValid` | 38 |
| `gRacePlayerHitCueId` | 35 |
| `ViewportState` | 34 |
| `GameTask` | 34 |
| `gMenuViewportCenterX` | 33 |
| `RelocatableHeapBlock` | 30 |
| `RacePlayer` | 29 |

These are information failures in the strictest sense — the model names an entity the TU does not
declare. `D_801124A0` is an address-named data symbol, which is the project's "prefer unknown"
default meeting a model that needs a name. The list is finite and can be worked down directly.

## `no text symbols`: two different things wearing one message

33 functions fail with only "Compiled object has no text symbols. Check for type conflicts or
include issues." That message covers:

- **Genuine no-ops** — `noopThreeArgs`, `noopFourArgs`. IDO emits nothing because the function does
  nothing. These are almost certainly the `bootThreadMain` class: **the target bytes may not be
  producible from C at all**, and they should be moved to a separate "impossible" bucket rather
  than counted as model failures.
- **libultra** — `__allocParam`, `__freeParam`, `osAiSetFrequency`, `_timeToSamples`. These are
  excluded targets anyway (`eval/clean_set.EXCLUDE_TU`), so they should never have been attempted.
- A remainder that needs individual triage: `runRenderCallbacks`, `setPackedMatrixTranslation`,
  `drawMenuFillRectangle`, `resolveAssetTableRelativePointer`.

## The refusal regression has a one-line cause

The working thread measured 4 refusals and 1 no-extract from a well-formed 24,769-char prompt,
against **0 refusals in 2,197 historical gpt-oss attempts**.

`solver/llm.py` documents both the mechanism and the fix (lines 112–125): a partial **assistant**
turn stops refusals outright — "9/9 → 0/9 measured on functions that refuse every draw" — and it
only works through `/api/chat`. Appending the same text to `/api/generate` puts it inside the user
message, where it does nothing ("18/18 still refused"). The branch is:

```python
if prefill or response_schema is not None:   # -> /api/chat, with an assistant turn
else:                                        # -> /api/generate, plain prompt
```

**`eval/trajectory_factory.py:504` calls `generate()` with no `prefill` and no `response_schema`.**
Every other LLM call site in the repo passes one: `solver/pipeline.py:280` and `:456` pass
`prefill=PREFILL`, and so do the pilots (`abi_leaf_pilot`, `callee_context_pilot`,
`modelrepair_ab`, `wavefront_mismatch_repair`, …). The factory is the only one that omits it, and
it checks `is_refusal` *after* the fact (line 518) instead of preventing it.

That is a hypothesis with strong support, not a proven fix: the factory's refusals should vanish
when it passes `prefill` and the request moves to `/api/chat`. It is a one-line change and a
nine-attempt test.

## Recommended order

1. **Fix the factory's `prefill` and re-run the 199.** They are ten days stale, the do-while
   lowering and the refusal fix both postdate them, and 173 already have enough attempts to be
   worth one more. Cheapest action available, and it needs no new engineering.
2. **Work the 193-symbol undefined list.** Finite, mechanical, and it unblocks 44 functions.
3. **Split `no text symbols` into impossible / excluded / real.** `noopThreeArgs` and
   `noopFourArgs` are probably unreachable, and counting them as failures corrupts every
   downstream aggregate.
4. **Triage the 63 syntax-only functions.** Largest clean bucket, and the least understood — the
   model is producing C that does not parse, which is a prompt/extraction problem rather than a
   compiler one.
5. **Filter libultra out of all admission accounting**, the same exclusion the learnability
   objective already needed.

## A note on the day-level compile rate

It swings from 13.0% (09-01) to 97.7% (08-30) and 95.1% (09-04). Do not read that as pipeline
progress or regression: different days ran different strategies, some of them deterministic and
non-LLM. Per-strategy rates are the only honest comparison, and they were not computed here.

---

# Addendum, 2026-09-16: capacity or assembly, and what was done about it

## Verdict: assembly. Capacity was never the constraint.

The prompt blocks for the failing functions are **0-1,666 characters** against a **131,072-token**
window. Three orders of magnitude of headroom. And `solver/prompt_budget.py` *raises*
`ContextBudgetError` rather than truncating, with the message "no evidence was truncated", so
nothing was being silently evicted for space either. The declarations were never in the prompt.

Confirmed directly by calling the context builder on the real failing functions:

| function | failed on | KB block | mentions it? |
|---|---|---|---|
| `resetViewport` | `ViewportState` | **0 chars** | no |
| `suspendGameTask` | `GameTask` | 188 chars | no |
| `updateCallbackTasks` | `CallbackTask` | 488 chars | no |
| `drawRaceIntroFlyoverActor` | `displayListValid` | 1,666 chars | no |

The mechanism: `build_prompt` assembles only binary-derived facts and then instructs the model to
*"Declare each callee with the argument and return types its use here implies."* The model invents
`GameTask`, which is the **correct** name from `include/game/engine/game_task_scheduler.h` -- and
`common.h` includes `PR/mbi.h` and `game/math/geometry.h` and nothing under `game/engine/`. The
model is penalised for knowing the domain and guessing right.

## The capability already existed and was simply not wired

`solver/project_headers.py` has **250 call sites** and a `prompt_context` whose documented job is
"render bounded project declarations without reading target C source". Measured against the same
seven functions it covers **6 of 7** -- identical coverage to the module written here first.

**This write-up originally added `solver/declarations.py` to do it. That was a duplicate and it
was deleted.** `PIPELINE_MAP.md` warns in as many words: "do not confuse an available module with
an enabled workflow or reimplement a mechanism already listed there." Two divergent paths for one
job is worse than no fix. The wire instead:

`solver/pipeline.py:build_prompt(..., declarations=True)` -> `project_headers.prompt_context`.

**Opt-in and default off**, because on SBK1 those headers are the decomp team's own reconstruction
-- `CLAUDE.md`'s header-assisted tier, which must be reported separately from SOLVED -- and
`eval/zero_token_harvest.py` excludes the same module deliberately. Tests in
`tests/test_prompt_header_context.py` pin both that it fires and that the default path is unmoved.

## Low-level: Ollama serving config

Applied at User scope and verified in `%LOCALAPPDATA%\Ollama\server.log`, not assumed:

- `OLLAMA_FLASH_ATTENTION=1` -> log: `llama_context: flash_attn = enabled`
- `OLLAMA_KV_CACHE_TYPE=q8_0` -> log: `llama_kv_cache: size = 1632.00 MiB (131072 cells, 12
  layers, 1/1 seqs), K (q8_0): 816.00 MiB, V (q8_0): 816.00 MiB`

At f16 that same cache is ~3,264 MiB, so this frees ~1.6 GB at full context. `gpt-oss:20b` is
MXFP4 (weights ~12 GB) and uses hybrid sliding-window attention, so only 12 of its layers hold a
full KV cache -- which is why 128k context was ever close to fitting on a 16 GB card at all.

**Caveat, stated because it is real:** `ollama ps` PROCESSOR readings were noisy across runs (32k
reported 17% CPU offload *after* the change, while the log for the same load shows
`offloaded 25/25 layers to GPU`). `nvidia-smi` on this machine reports garbage -- 17,592,181,866,707
MiB used on a 16,303 MiB card -- so it could not be used as the metric. The log lines above are the
authoritative evidence that the settings took effect; the throughput benefit was **not** measured.

`OLLAMA_NUM_PARALLEL` was deliberately **not** set: it trades concurrency for context, and nothing
here established whether the pipeline issues concurrent requests.

## Test state

Full suite in WSL: **3247 passed, 3 failed, 3 skipped**. The three failures are environmental and
predate this work: two need `numpy` (absent from the venv) and one asserts a Windows path
(`C:/p`) is absolute under `PosixPath`. Two `test_flywheel` failures *were* caused by this change --
they stub `kb_context.for_function` with `lambda *_: "KB"`, which rejected the added keywords; the
stubs now take `**__`. Do not use this section as a trend: it is a snapshot.

---

# Addendum 2: end-to-end, and what it does NOT show

## The compiler-level mechanism works

Controlled A/B on the real failing artifact for `suspendGameTask`, no model involved:

| arm | result |
|---|---|
| A: historical source unchanged | `'GameTask' undefined`, `Selector requires struct/union`, `'var_v0' undefined` |
| B: `prompt_context` output prepended | unchanged -- because that output is **prompt text, not C** |
| C: `#include "game/engine/game_task_scheduler.h"` added | **`GameTask` cleared**, `var_v0` cleared, one unrelated error left |

So supplying the header does resolve the motivating residual. B is worth keeping: it shows
`prompt_context` hands the model the header *contents* labelled `HEADER <path>:`, but the prompt
never tells the model to `#include` that path. Including is what works; pasting is what fails.

## The generation A/B does NOT show a benefit

Same function, same route, same sampling, PREFILL on both arms so the refusal regression is out of
the picture:

| arm | n | compiled | best score |
|---|---|---|---|
| declarations OFF | 2 | **1** | **74.10** |
| declarations ON | 2 | **0** | 0.00 |

**n=2 per arm cannot support a conclusion, and this is not evidence that declarations help.** If
anything it leans the other way. What it does establish is larger: the OFF arm compiled at 74.1
with **no `GameTask` error at all**. The historical failure that motivated this whole thread no
longer reproduces under the current prompt.

## Which supports the staleness hypothesis

A sample of the never-compiled set, one candidate each, current pipeline, no headers: **2 of 6
compiled**. Caveat that must travel with it: sorting the set alphabetically drew `libmus` internals
(`Fdefa`, `Fdistort`, `Fdrums`, `Fenvelope`, `Ffor`, `Fgoto`), not game functions, so the sample is
not representative of the population the analysis was about. Combined with `suspendGameTask`
compiling at 74.1 unaided, the direction is consistent and n is far too small to settle it.

**Therefore: the header-assisted wiring is not demonstrated to fix admission, and the honest
reading is that most of the 199 are stale rather than blocked.** The decisive experiment is a
proper re-run of the 199 -- game functions only, several samples each, `declarations=False` -- which
has not been run. Until it has, do not quote the header work as an admission fix.

# Larger local model and larger context on the large-function tier

2026-09-12. Two lanes run outside the frozen campaign, each pausing it, owning the
single GPU, then restoring the campaign model and resuming (`run_lane.py`).
Development evidence on DEV-eligible functions; not a held-out benchmark.

## Lane 1: qwen3:32b (dense), 32K context -- aborted

Queue: `queue.json`, unmatched compiling functions over 80 instructions, campaign
score descending, each pinned by SHA256 to the campaign's candidate.

- Memory: 27.2 GB footprint at 32K (8 GB KV cache, 64 full-attention layers), 13.7
  GB offloaded to RAM. Generation **3.2-3.5 tok/s** versus ~146 for gpt-oss:20b.
- The memory brake (watchdog `STOP` file at 0.8 GB free) aborted it after ~60 min
  on the first function, unloaded the model and resumed the campaign as designed.
- `updateCourseCollectibleSprites`: 5 proposals, all compiled, **all at the parent
  score exactly** -- type tweaks and an unused struct, justified as aligning
  register allocation.

Not sustainable on a 16 GB card while the machine is in use.

## Lane 2: gpt-oss:20b, fixed 64K context -- completed

`SOLVER_FIXED_CONTEXT=65536` (new, opt-in, `solver/prompt_budget.py`). Kernel run in
campaign shape (`--resilient --include-header-context --retry-invalid`, beam 3).

**Queue selection.** Campaign receipts show 120 functions whose model calls failed
with `ContextBudgetError`. Of the top 30 still unmatched, a GPU-free dead-port
probe found **17 still refused at 32K today**; 13 now fit (compaction shipped since).
The three largest were confirmed to fit at 64K. `queue_context_confirmed.json`.

**Cost of 64K:** 11.92 GB, fully in VRAM (32K: 11.87 GB). 150 tok/s. The lane ran
20 minutes end to end.

**Refused prompts were not truly oversized.** The first refused function's real
prompts were 20,724-22,731 tokens: with the 6,000 output allowance they fit 32K.
The guard's UTF-8-bytes/2 estimate overestimated by at least 24% here. 64K removes
the refusal at no memory cost either way.

### Result

| | |
|---|---|
| functions | 17 |
| model calls | 96 |
| exact | **0** |
| score improved | **1** |
| invalid proposals | 49 (51%) |
| distinct compiled children vs parent | 18 identical, 11 worse, 2 better |

**Invalid proposals (49):** 29 ambiguous anchor (`old substring occurs N times`, 4 of
them quoting read-only assembly), 8 no-op, 8 empty `old` with no insertion point,
2 public ABI lock, 1 overlapping, 1 malformed. The kernel supports line-slot edits
(`L<n>`) that avoid ambiguity; the model does not use them.

**Codegen-neutral edits:** 58% of distinct compiled children produced exactly the
parent score. Checked directly on `initScheduler`: an added unused `u32 dummy[6]`
recompiled to identical 448-byte `.text` and byte distance -- a real, inert edit,
not a compile cache returning the parent. The kernel does not tell the model its
edit changed nothing.

**The one improvement** (`updateRacePlayerInput`, 86.029 -> 88.094) replaced m2c
pointer arithmetic with array indexing:

```c
-  player->disabledInputFlags = *(&gRacePlayers->inputFlags + (temp_v0 * 0x60C));
+  player->disabledInputFlags = gRacePlayers[temp_v0].inputFlags;
```

That is a mechanical pattern (stride equals the struct size), the same "pointer
arithmetic instead of array access" residual Snowboard Kids 2 reported in its long
tail. It should be a deterministic rewrite, not a model call.

## Conclusions

1. Neither more parameters nor more context produced a match. The 32B and the 20B
   made the same class of inert edits.
2. Half the calls on large functions are lost to edit addressing, not reasoning:
   ambiguous anchors grow with function size.
3. Candidate harness changes, each testable on this same queue:
   - on an ambiguous anchor, return the matching line numbers and retry, or
     disambiguate from context, instead of discarding the proposal;
   - label object-identical children and feed "no codegen change" back;
   - a deterministic `*(&base->field + i*sizeof(T))` -> `base[i].field` rewrite.
4. The campaign could run at 64K at ~50 MB extra VRAM, removing 32K refusals. That
   needs the campaign server's `OLLAMA_CONTEXT_LENGTH` and the frozen code's cap
   changed through the amendment protocol; not done here.

Attempts are in `~/decomp/kb-sbk1-bigmodel-20260912.sqlite` (a copy). Per-function
receipts, logs and best sources are under `gptoss-64k/`.

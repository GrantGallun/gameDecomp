# Protocol: binary-derived type contexts on every never-matched function

Written 2026-09-24 before `capability.py` ran, while the frozen BINARY arm's CHECK run
(`context-ablation-20260924`) was in progress.

## Set
Every function with a workspace under the target repo's `nonmatchings/` and no exact attempt in the production KB
(`attempts.exact = 1`). The reference source of these functions is never read. Sealed held-out functions are included:
this is a deterministic evaluation run, with no training and no tuning on them.

## Method (frozen; `context-ablation-20260924/freeze.json`)
For each function: `binary_context.decls` (struct-identity FIT-chosen variant, binary facts only) behind the clean
prelude (public libultra/SDK headers only, PROTOCOL A6 there), m2c `--target mips-ido-c` with that context, the
definition compiled STANDALONE (prelude + declarations + definition) in a copied workspace with the function's recorded
recipe. With a stride found in m2c's pass-one output, a second pass declares that global as an array. Harness outcome:
exact / compiled / not compiled / m2c failed.

## Confirmation and recording
Each harness-exact source is recompiled independently through the project's official path
(`eval.search_evolution.compile_logged` in an isolated repo, the frozen code root used by earlier recordings), which
applies the frontend gate. If the object is exact and the gate refuses the C, the existing `frontend_type_repair` is
applied to the gate's own diagnostics (up to two rounds, as for `finishCurrentRdpTask` and
`__osSetGlobalIntMask`). Only gate-passing exact objects are recorded, with strategy
`binary-types-20260924:source-independent`, after checking that the source has no `#include "game/` and no type name
defined only in the reference's `src/` or `include/game/**`. The ratchet check (the exact set only grows) guards the
write.

## Reported
Harness outcomes overall and by size; confirmed exact; recorded; `eval.status` before and after; for the unsolved
restart-round-3 population, how many of the 206 were closed. No threshold: this is a capability measurement, and it is
reported whatever it is.

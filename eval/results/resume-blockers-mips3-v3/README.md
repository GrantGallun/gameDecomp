# Runtime-helper blockers: isolated repair results

September 8, 2026. The main game-wide campaign continues using its original frozen code. These are separate development results, not additions to its current match count.

## Changes and outcome

The compiler adapter rejected the game's explicit `-mips3 -32` recipe for `build/src/ultra/libc/ll.o`. The adapter now accepts that precise configuration while continuing to reject unsupported ISA/ABI combinations and object postprocessors. A real IDO probe compiled checks that pointers and long are 32 bits and long long is 64 bits, and emitted the expected wide multiply instructions.

Removing the recipe gate alone left all ten generated drafts noncompiling (v1). The existing complete-instruction recognizer now also generates arithmetic candidates through `compile_recovery.variants`, using the target's full instruction stream and o32/compiler gates. It recognizes seven arithmetic shapes shared by eight helpers; names do not choose the operation or signedness.

| Function | Compile | Frontend | Object sections/relocations exact |
|---|---|---|---|
| `__ull_rshift` | Pass | Pass | Yes |
| `__ull_rem` | Pass | Pass | Yes |
| `__ull_div` | Pass | Pass | Yes |
| `__ll_lshift` | Pass | Pass | Yes |
| `__ll_rem` | Pass | Pass | Yes |
| `__ll_div` | Pass | Pass | Yes |
| `__ll_mul` | Pass | Pass | Yes |
| `__ll_rshift` | Pass | Pass | Yes |
| `__ull_divremi` | Fail | Fail | No |
| `__ll_mod` | Fail | Fail | No |

The eight exact functions account for **452 bytes of inventoried function extents**. The two remaining helpers have more complex arithmetic/output patterns beyond the closed recognizer. All ten targets are eligible DEV functions, not members of the campaign's frozen held-out exclusions.

No LLM calls, reference C function bodies, production DB changes, game-source integration, or runtime-interpreter admission were used. Existing initial m2c drafts and project headers remain part of this development setup. Shift/division behavior on undefined C inputs is not separately claimed; the compiler/object certificate checks the resulting binary. The complete current live input-pin set captured before the probe was verified unchanged afterward.

## Validation and receipts

- [Full report](report.json): ABI probe, copied input hashes, per-function intake results and exactness certificates; `live_pins_unchanged_after=true`.
- [Private attempt DB](probe.sqlite) and `*.candidate.c`: durable reconstruction sources and verification attempts. Some diagnostic paths refer to temporary workspaces removed after the probe; retained candidates and hashes identify the results.
- [Full regression log](../resume-blockers-tests.log): **2,018 passed in 37.68 seconds**.
- Follow-up catalog/project-consistency tests: **43 passed** after registering the measured pattern.
- [Replay tool](../../experiments/campaign-gap-audit/probe_mips3_recipe.py): accepts `--output` for a new receipt directory and refuses overwrites.
- v1 preserves the result of the compiler-gate fix alone; v2 preserves the first two exact recoveries; v3 contains all eight.

The active campaign currently also has hardware-backend, SDK control-flow/relocation, missing-symbol, and object-postprocessing blockers. Those are distinct work items and are not resolved by this change. The new recovery is ready for a subsequent frozen replay/fork after the baseline campaign; do not edit its live snapshot or merge these counts into its baseline result.

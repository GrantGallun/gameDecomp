# Promoted mechanisms, population rerun: 8 -> 12 exact, 0 losses

Same 224 frozen sources, scheduler and 32-call budget as stage-2 `routed`; only the code differs (code-v3 =
the four machinery fixes, `evidence_site`, `frontend_type`, and the scheduler passing each node's verdict).
6,207 compiles, 0 receipt errors, no model calls (`analysis.json`).

| | routed (stage 2) | promoted |
|---|---:|---:|
| exact /224 | 8 | **12** |
| gains / losses | | 4 / 0 (sign test p = 0.0625) |
| unsolved functions with a better best score | | 36 better, 6 worse, mean +0.65 |

Gains and their paths, all found unattended from the frozen starting source:
- `MusHandleAsk`: residual_evidence -> **frontend_type**
- `createGameTask`: stmt_order -> **frontend_type**
- `osGetThreadPri`: parameter_reuse -> **frontend_type**
- `dispatchRacePlayerMode07CourseObject`: **single_use** (fixed yesterday)

All four were already recorded on 2026-09-22 by the experiment scripts that discovered these mechanisms, so the
inventory is unchanged (373 byte-exact, SOLVED 273). What changed is that the solver now reaches them itself.
`MusAsk` and `releaseRelocatableHeapBlockMetadata` needed longer chains than 32 compiles allowed.

## Machinery card of the new families (this run)

| | applications | broke | no-op | improved when acting | exact |
|---|---:|---:|---:|---:|---:|
| `evidence_site` | 209 in 69 functions | 1% | 8% | 50% | 0 |
| `frontend_type` | 292 in 61 functions | **22%** | 75% | - | 3 |

`evidence_site` behaves as measured in the experiment. `frontend_type` does its job where objects already match,
but elsewhere spends compiles: its type fixes are meant to leave bytes unchanged (the no-ops), and its call-site
prototypes break the build when the result is dereferenced or the callee is called with differing argument
counts. Next: fire it only when the object certificate already matches, and decline prototypes the call sites
cannot determine.

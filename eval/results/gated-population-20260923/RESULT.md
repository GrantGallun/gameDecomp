# Measured gates, validated: 12 -> 13 exact, 0 losses, installed

`python -m eval.mechanism_roadmap` learned three gates from the promoted run's worlds (cross-fitted by function
halves; `../mechanism-roadmap-20260923/`):

| family | skipped when | applications | improving |
|---|---|---:|---:|
| `commutative` | parent rejected by the frontend gate | 323 | 1 |
| `frontend_type` | dominant residual is register allocation | 179 | 1 |
| `local_type` | dominant residual is ordering | 74 | 0 |

Rerun with them frozen in (code-v4), same 224 sources and 32-call budget, paired against promoted:
**13 exact vs 12, 0 losses**; 8 unsolved functions better, 0 worse; 6,127 compiles vs 6,207.
The gain is `releaseRelocatableHeapBlockMetadata`, the four-step chain found by hand yesterday, now found
unattended within budget: evidence_site -> field_local -> owner:drop_mask -> frontend_type.
It was already recorded, so the inventory stays 373 / SOLVED 273.

Acceptance (fixed in `freeze.py` before the run): no exact lost. Met, so the gates are installed in
`solver/family_gates.json` with this provenance.

# Residual-guided byte matching repair

The earlier run produced no new byte matches and did not improve the five remaining seeds. Its structural-residual path returned only the small code-shape family, bypassing existing comparison, expression and lifetime repairs. It also generated do-while forms rejected by the project's compiler helper and repeatedly expanded sources with identical assembly.

The revised implementation includes those repair families for structural residuals, adds direct-line-guided field width/unsigned-load repairs, typed subexpression materialization and disjoint local coalescing. Direct source evidence ranks candidates before truncation. The frontier allows two expanded representatives of an assembly output, retaining one neutral continuation while bounding inert repetitions. Production loop alternatives avoid do-while. OSS remains the last fallback; these measurements use no model calls.

| Function | Seed score | Revised best | Semantic panel | Result |
|---|---:|---:|---:|---|
| releaseSoundEffectHandleNode | 99.615 | 100.000 | 67/67 | New object-section exact match |
| createCallbackTaskPreservingArgs | 90.291 | 93.234 | 3/3 | Improved |
| __MusIntRandom | 98.013 | 98.333 | 256/256 | Improved |
| calculateRaceTimerDelta | 94.324 | 94.324 | 6/6 | Unchanged |
| Fendit | 88.000 | 100.000 | 5/5 | Reproduced previously known match |
| updateRacePickupIdle | 91.064 | — | 29/256 at baseline | Excluded: baseline failed expanded panel |

The main run is in [v4 summary](../direct-source-semantic-cohort-v4/summary.json). It compiled and semantically passed all 257 tested candidates for the five admitted functions. The final direct-ranking implementation was confirmed here on callback (88 candidates) and release (2 candidates); see [final summary](summary.json). Every candidate in this confirmation compiled and passed its panel. The confirmation reused the earlier isolated experiment database; the main run used a fresh production-database backup. Production source integration was not performed.

Release's exact edit coalesces `temp_v1_2` into `temp_v1`, removing the final three register mismatches. There was no exact attempt for this function in the starting database through attempt 31094. Its certificate reports `object_sections_exact`: allocated code/data/BSS and relocation expressions match in the same link environment. This is not a whole-ROM certificate. The inherited seed's pointer/integer declarations still fail the separate strict Clang frontend check, so the artifact is not claimed to be ready for source integration.

Callback gains combine a target-backed unsigned priority load, halfword active-field store and statement reordering. These representation repairs are target-directed experiments, not universal equivalence claims based on a small semantic panel. RNG gains materialize a typed subexpression without reassociating floating-point arithmetic. Candidate acceptance continues to require compiler verification and semantic replay.

The RNG and pickup saved stress receipts now contain 256 cases, versus 7 and 5 respectively in the prior experiment. Thus their panels are not an identical historical A/B comparison. Seed source identities and starting scores are unchanged; each revised run recompiles and rechecks its own baseline. The runner now snapshots complete case definitions for future runs.

Validation: 131 targeted tests passed under WSL, including native compilation/execution of generated variants, direct-attribution freshness, bounded frontier continuation, semantic-regression rejection, OSS fallback integration, and existing pilot/transition regressions. Real measurements use the project's IDO compiler and binary differential executor.

Byte comparison is linear in the compared object size for each compiled candidate. Generating, compiling and semantically testing the candidate space is not an overall O(N) guarantee; the search remains bounded and may exhaust without finding a match.

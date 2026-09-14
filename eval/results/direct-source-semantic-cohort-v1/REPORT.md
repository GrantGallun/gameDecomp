# Semantic-match cohort test

Six real saved semantic-pass/nonexact candidates were recompiled with the production IDO recipe and tested with the deterministic search. Attempts are in an isolated SQLite backup.

| Function | Cases passing | Candidates | Compiled | Byte score | Direct mismatch mapping |
|---|---:|---:|---:|---|---:|
| calculateRaceTimerDelta | 6/6 | 4 | 4 | 94.324 → 94.324 | 19/19 |
| __MusIntRandom | 7/7 | 6 | 4 | 98.013 → 98.013 | 10/10 |
| updateRacePickupIdle | 5/5 | 16 | 16 | 91.064 → 91.064 | 152/152 |
| Fendit | 5/5 | 2 | 2 | 88.000 → 100.000 | 1/1 |
| createCallbackTaskPreservingArgs | 3/3 | 6 | 4 | 90.291 → 90.291 | 69/69 |
| releaseSoundEffectHandleNode | 67/67 | 51 | 51 | 99.615 → 99.615 | 3/3 |

**85 candidates; 81 compiled; all 81 compiling candidates passed their semantic panels.** Of these, 44 proposals came from the new code-shape generator. Four candidates were rejected by the existing do-while prohibition.

**No new byte-exact functions.** Fendit reproduced an existing exact solution (88% → 100%) via `fuse-store-load-return-postincrement`. All other best scores were unchanged. The Fendit object certificate verified allocated sections and relocations; this does not constitute whole-ROM integration.

**Direct attribution: 254/254 candidate-side mismatches.** The test exposed and fixed a closing-brace epilogue filtering bug affecting four instructions. Target-only deleted instructions have no candidate address and remain explicit gaps.

The callback and release functions were replayed on their actual saved passing panels. Initial broader census trials included target memory faults; those trials did not enter the semantic-pass search. Passing these finite panels is not a universal semantic-equivalence proof.

The test used no model tokens and did not integrate source into the game. It establishes working direct attribution and verified screening, but does not demonstrate a new-match gain or isolate the benefit of ranking without an A/B comparison.

Artifacts: [final summary](final-summary.json), [attribution audit](attribution-audit.json). Per-function receipts are under this directory and the supplemental `direct-source-semantic-cohort-v2` / `v3` directories.

# Last-push search and validation

Implemented bounded disjoint two-edit candidates, floating-point division staging without reassociation, signed quotient/remainder reuse, and configurable frontier expansion limits (CLI: `--exactness-expansions`, range 1?16). Added target-backed low-byte selector repair and explicit masked parameter storage. The local OSS model remains the final layer after deterministic search. Receipt writes retry transient Windows/WSL reader locks while preserving atomic replacement.

The callback test builder initializes valid free-pool, counter, sentinel and linked-list states. Its 76 cases cover every reachable target instruction and branch edge, exercise all seven allocation types, exhausted counters/pool, empty/front/middle/tail insertion, priority boundaries and upper type bits. The earlier generic 128-case panel passed but covered only 17.16% of target instructions. The structured panel exposed two real failures in the previous 93.234 candidate: dispatch used a halfword instead of the target's low byte. A target-backed selector repair passes all 76 cases. Exposing the existing 16-bit parameter mask through its storage type then improves the score to 93.702.

Release's inherited pointer/integer declarations were corrected. Its candidate remains object-section exact, passes the strict frontend check, and passes all 128 stress cases with complete target instruction/branch coverage. These candidate artifacts have not been integrated into the production translation units; object-section exactness is not a whole-ROM build certificate.

RNG's paired loop/staging candidate improved from 98.333 to 98.590. The first run was interrupted by a receipt rename lock after the candidate passed its 256-case panel. It was recovered from the isolated attempt database and submitted for a fresh baseline and bounded continuation.

Regression validation: 139 tests passed under the installed WSL compiler environment, including compiled execution of new arithmetic variants, pair bounds/overlap guards, mask/prototype handling, full existing pilot and OSS fallback tests, and transient receipt-lock recovery.

## Results

| Function | Previous score | Verified best | Semantic cases |
|---|---:|---:|---:|
| createCallbackTaskPreservingArgs | 93.234 (failed 2 new cases) | 93.702 | 76/76 |
| __MusIntRandom | 98.333 | 98.590 | 256/256 |
| calculateRaceTimerDelta | 94.324 | 94.324 | 6/6 |
| releaseSoundEffectHandleNode | 100.000 with frontend errors | 100.000 with frontend pass | 128/128 |

The RNG gain was reverified in a fresh baseline after recovery. No additional function reached exactness in this push. Release retains its previously discovered exactness with corrected declarations.

Live gpt-oss:20b fallback was exercised on all three non-exact functions (10,644 recorded tokens). Callback produced a valid proposal without verified progress; RNG ended after compiler errors; timer ended after invalid/no-op patches. None supplied a byte improvement. The retained callback artifact comes from the independently compiled and fully replayed masked-parameter alternative, not the lower-scoring fallback result.

The callback fallback run exposed a pre-existing champion-selection issue: even after semantic tests passed, runtime instruction-count diagnostics could outweigh byte score. The final implementation ranks exactness/byte score first among semantic-clean candidates and retains separate neutral frontier exploration. A regression specifically covers the observed 93.702 versus 87.816 ordering.

## Receipts and candidates

- [Callback structured repair and storage audit](../last-push-callback-storage.json)
- [Callback candidate](createCallbackTaskPreservingArgs.c)
- [Callback live OSS experiment](../last-push-callback-v3/summary.json)
- [RNG re-verification and live OSS experiment](../last-push-rng-v3/summary.json)
- [RNG candidate](__MusIntRandom.c)
- [Timer experiment](../last-push-timer-v3/summary.json)
- [Timer candidate](calculateRaceTimerDelta.c)
- [Release strict-frontend and exact-object audit](../last-push-release-clean.json)
- [Release candidate](releaseSoundEffectHandleNode.c)
- [Structured callback cases](../last-push-callback-v3/createCallbackTaskPreservingArgs.cases.json)


Final champion-order confirmation: 53 candidates compiled; 49 passed the 76-case callback panel and four semantic regressions were rejected; the 93.702 starting champion was retained, with zero model tokens. See [confirmation summary](../last-push-callback-final/summary.json).

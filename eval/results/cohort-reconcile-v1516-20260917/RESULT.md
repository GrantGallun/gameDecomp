# Round 9: reconcile the FINAL nodes of a running ledger — +12

The round-8 report said the runs were slow and the round would be a wait. That was the wrong call, and
the fix is a distinction worth keeping: **a ledger does not have to be finished to be readable.** The
`summary` block is written when a batch ends, but every node carries its own state as it settles, so
nodes already marked `object_exact` are final while the run is still going.

Reconciling v15 and v16 mid-run, counting only nodes whose state is `object_exact`:

| | |
|---|---|
| nodes claiming object_exact | 14 |
| already counted (correctly skipped) | 2 |
| **REPRODUCED-EXACT** | **12** |

`createThrownTrailImpactProjectile`, `drawRaceTypeSelectCursor`, `drawShopMenuPromptPanel`,
`func_80061A98`, `initCourseSelectCourseDescription`, `initTrainingCourseEndingDialog`,
`openEndingCreditsIfUnlockedFlow`, `startRaceGameplayFlow`, `updateEndingJamPhase3FAnim3`,
`updateRaceIntroFlyoverShortPanFinal`, `updateTitleScreenStartPrompt`, `waitEndingJamPhase2F`

| | round 7 end | now |
|---|---:|---:|
| byte-exact | 275 | **287** |
| SOLVED | 209 | **221** |
| attempts logged | 51,174 | **51,186** |

## The contention question, answered

The two concurrent runs are **not duplicating work**: their node sets are disjoint (24, 24 and 36
nodes, zero intersection across all pairs). The campaign's selection receipts prevent re-selection, so
the cost of running two at once is shared machine time, not repeated compiles — which means the round-7
"wasted work" phrasing was too strong, and the single-flight rule is about wall time only.

The node counts also confirm the widening is real: **36 nodes at per-stratum 12** against 24 at 8.

## Method note

Counting only settled nodes is the same discipline as reading a receipt's scope before believing it: the
question is not "is this file finished" but "is what I am reading final". A `pending` node is not
evidence; an `object_exact` node is, whenever it was written.

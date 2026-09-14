# Callback follow-up swarm

The second three-agent team improved callback from 94.723 to 97.454. The retained candidate passes all 76 structured semantic cases. It is not byte-exact; the remaining gap is 2.546 similarity-score points. Similarity score is not the literal percentage of equal bytes.

The winning source independently guards `gCallbackTaskActiveListSentinel.next`, reloads through `insertAfter`, then traverses with a `for (;;)` loop whose null check is at the bottom. Two agents discovered the same improvement independently. This restores most of the target's separate head-reload sequence.

The remaining structural difference is explicit address materialization. The target builds the sentinel address with `lui` plus `addiu`, then loads at offset 4. IDO folds the candidate's offset into the load relocation. Register allocation differences remain through selector dispatch, pool lookup, and list handling; changed instruction positions also affect branch offsets. See [remaining instruction diff](remaining.diff).

## What was tested

- Reload/alias agent: 80 builds, 97.454 winner passes 76/76. Twenty further address, cast, temporary and comma-expression probes did not improve it.
- Dispatch/lifetime agent: 58 bounded alternatives. Index inlining alone improved 94.723 to 95.007 and passed 76/76, but composing it with 97.454 regressed. It was not retained.
- Control/store agent: 49 builds including controls; independently reached 97.454. Twelve allocation/index variants and 23 other field-store orders did not improve that winner.
- Coordinator: 14 additional sentinel storage/type-view and link-field representation probes all remained 97.454.

The positive pure-C reload operator is integrated into the normal deterministic candidate library. Its recognizer checks the head base's reaching initialization and rejects unknown intervening effects. The pipeline requests only the first pure-C winner; unproductive qualifier, dispatch and store-order experiments remain outside the production search. No compiler flags, forbidden assembly, build guards or production translation units were changed.

Regression validation: 169 tests passed, including the new reload recognition/reaching-definition guards, agent operator checks, and existing semantic rejection, source attribution, frontier, replay-cache and OSS fallback tests.

## Artifacts

- [Retained candidate](createCallbackTaskPreservingArgs.c)
- [Full 76-case replay](../callback-finish-reload-v1/replay.json)
- [Reload report](../callback-finish-reload-v1/REPORT.md)
- [Dispatch report](../callback-finish-dispatch-v1/REPORT.md)
- [Control report](../callback-finish-control-v2/REPORT.md)
- [Integrated search confirmation](../callback-finish-integrated/summary.json)

Integrated confirmation: the normal deterministic search reproduces 97.454 from 94.723 in 28 candidates, retains all 76 semantic passes, and uses zero model tokens.

# Deterministic frontier and compiler-effect work, September 26

Latest status: continuation completed four full batches and 15 jobs of batch 5,
then paused after a worker exception in `mips_differential` (`math.trunc(infinity)`
while interpreting `cvt.w.s`). Native checkpoint **29289** has **998
object-exact/integrated** and **24 function-exact pending integration**, up from
972 and 22. The receipts record 415 completed jobs, 12,727 imported attempts,
no exact losses and no model-call/proposal increase. Three jobs remain in
checkpoint inflight metadata; two have raw receipts and require normal recovery.
See `after-fix/batch-0005/canary.json` and its controller log. No fix or restart was
performed during the subsequent read-only design discussion. Earlier snapshots
and their measured provenance are retained below.

## Executed baseline

The first two normal frozen-controller batches completed 30 work items, importing 188 durable attempts. All 972 prior object-exact/integrated functions and the separate 22 function-exact pending integration survived. Zero new exacts, zero model calls/proposals and zero newly parked nodes. Work comprised 27 semantic revalidations, two stack-layout visits and one frontend fix-it. One semantic job consumed roughly 16 minutes; the run drained normally without killing it. [Progress](progress.json), [batch audit](audit/report.py), [final baseline](final.json).

## Two concrete delivery defects

The old register-search adapter compiled proposals into a discarded in-memory attempt database and omitted source attribution/frontend/compiler-recipe evidence when passing results to the generator. The delivered module therefore could silently decline evidence-driven families and lose failure observations. The fix persists all probe attempts in the worker DB with actual parent lineage, passes fresh evidence, and refuses frontend-rejected raw exacts as search completion.

Separately, `audioThreadMain` has one structural fault and no register fault, so register search excludes it. Its exhausted `local_rewrites`/`deeper_composition` profiles use another generator, and binary retry only redrafts/rescores. The new versioned operand route gives compiled, frontend-valid nodes with at most two structural faults one bounded visit to the full measured mutation stream. Existing cheap repairs and incumbent alternatives remain; no model budget resets or historical winning source imports.

## First-principles compiler experiments

`audioThreadMain`: the retained candidate emits `move s0,s4` where the target emits `li s0,1`. A normal mutation stream changed one local from `s32` to `u32` and reproduced ordinary frontend/object exactness after 13 proposals. This function was already exact in the research ledger, so this is a delivery reproduction, not new project coverage. [Production API canary](audio/production-canary.json).

`releaseSoundEffectHandleNode`: three register operands came from one live range. The trace showed that the candidate left v0 available, so the allocator selected it; the target uses v1. An initial lifetime extension moved all three operands as predicted but introduced a tail-store difference. Three follow-up zero-expression edits either removed the needed lifetime or added control flow. The successful intervention reused one compatible pointer local across mutually exclusive branches, transmitting the other arm's v0 conflict while preserving the literal-zero store. Ordinary frontend and object-section certification accepted the generated candidate. This function is absent from both baseline raw exact ledgers. [Prediction, failures and exact receipt](register/RESULT.md).

The guarded `local_web_merge` proposal generator now offers this intervention without function/name special cases. Tests cover actual stream firing, scope, shadowing, incompatible types, address escape and qualifier declines. This is a confirmed development case with inherited project-header assistance. Transfer, independent clean capability and whole-ROM integration are not established by it.

## Verification and deployment

See [fix scope](FIX_PLAN.md), the [applied amendment](../resume-pipeline-20260908/revisions/20260926-operand-delivery/amendment.json), and the independent [review](REVIEW.md). Seven code files and one binary link-map input were applied; unrelated main/frozen drift remains frozen. No miner/KB implementation or production game source changed. The amendment verified unchanged node contents, inventory and model identities, restored exact code/input pins, and retained all 972 object-exact/integrated functions at checkpoint 28457.

Final manifest `9c7d9b96e75f4548ceb2aedf849a884ca976524a3998f376d415a727c7d89056`
binds seven code files, one map pin and 3,306 unchanged pins at checkpoint 28456.
The copied frozen package passed **190 tests, one skipped**; three dry amendment
tests passed. The independent [review](REVIEW.md) found three Important issues:
canonical log ID remapping, direct generator helpers missing from the route
revision, and missing pipeline-map documentation. All were fixed; new regression
tests failed before the code changes and passed afterward.

The final [two-worker canary](audio/controller-canary-v2.json) selected
`operand_repair@f4aa2692fc97a7ad` through normal scheduling for both retained
candidates. Both reached frontend-passing `object_sections_exact`; 18 attempts
and 18 actual parent edges were imported, with zero model work or inflight jobs.
Concurrent snapshots forced all 14 audio attempt IDs to change on import; the
strict audit verified every canonical `receipt_id` and parent edge. Release's
four logs also resolved. Previous job histories were preserved. V1 artifacts
remain archived, and no private winning source was imported into the live run.

After canary/review acceptance and successful application, `resume_after_fix.py --run` was launched to continue the deterministic queue with fresh baseline/receipts under `after-fix/`. It uses 100-item controller batches, three workers and unchanged per-function budgets. Model calls, integration and runtime capture remain disabled for this session. Both match membership and input hashes are checked, and any newly parked node stops the next batch for diagnosis. Create `after-fix/STOP` for a batch-boundary stop; the native durable pause marker also stops active dispatch. `live_audit.py` can compare a coherent current checkpoint against the baseline without hydrating every node; completed batch receipts remain the final ratchet and dispatch accounting.

### Live continuation snapshot

At checkpoint **28652**, the normal live controller has completed **97 work items**
and imported **1,670 attempts** since amendment. Object-exact/integrated membership
is **972 to 982**, with no lost exacts, newly parked nodes or model activity.
The separate 22 function-exact pending integration and 43 parked nodes are
unchanged. The 100-item batch is still active; this is a coherent checkpoint
observation, not a completed-batch or exhausted-frontier claim.

| Accepted live function | Logged mechanism | Provenance |
| --- | --- | --- |
| `alLink` | statement-order proposal | already in a baseline raw exact ledger |
| `updateEndingSlashSlideLeftToMarker` | attributed signed-comparison proposal | already in a baseline raw exact ledger |
| `updateRaceCameras` | indexed pointer form | already in a baseline raw exact ledger |
| `osViSetEvent` | `-O1` register-local declaration | already in a baseline raw exact ledger |
| `releaseGameTaskById` | repaired register-search adapter | already in a baseline raw exact ledger |
| `updateRaceSetupNamePlateSlideIn` | typed reread | already in a baseline raw exact ledger |
| `updateRaceSetupNamePlateSlideOut` | typed reread | already in a baseline raw exact ledger |
| `releaseSoundEffectHandleNode` | local-web merge | absent from both baseline raw exact ledgers |
| `updateCourseDetailsPreviewTile` | pure local inline | absent from both baseline raw exact ledgers |
| `updateEndingCreditsTommyBigBurst` | pure local inline | absent from both baseline raw exact ledgers |

Seven gains reproduce previously recorded raw exacts; three are new against both
baseline ledgers. These retain the campaign's source/header assistance and are
development results, not clean held-out capability. Release's accepted live
attempt **167205** has the same generated hash `6a1b329a...` as the predicted private
intervention, reached through the ordinary deployed `local_web_merge` route.
The private audio confirmation remains separate until its normal live visit
finishes. See the preserved [checkpoint audit](after-fix/checkpoint-28652.json)
and refreshing [live audit](after-fix/live-audit.json). Each completed batch keeps
its own receipt. The
initial continuation queue had 1,014 eligible pending nodes, including 151
operand visits and 490 binary-type visits. These are opportunities, not predicted
matches.

## What blocks progress now

The immediate blocker was delivery: a working mutation stream did not reach a retained near-match, and another adapter discarded evidence and failure records. These are concrete implementation defects. The next blocker is search coverage: which source transformations alter the compiler decision responsible for a remaining difference without introducing collateral differences? The release-node experiment supplies one measured answer, not a universal allocator solution.

Keep the unattended deterministic queue running, deduplicate its exact gains against both baseline ledgers, and measure yield per mechanism. A fresh trace of `drawControllerPakFileDeleteConfirmOptions` corrected the coarse census diagnosis: its only differences are two `0x80` loads exchanged across a branch delay slot, while later uses have the target registers. One predeclared first-arm assignment reversal failed at 99.333, disturbing later register roles and stack slots; that exact edit was already proposal 1 in the existing stream. The next useful hypothesis must control the initial scheduling choice without disturbing later allocation, not duplicate that generator or assume the `split` label proves a lifetime cause. See the bounded [trace and rejected probe](range-split/RESULT.md). For large structural residuals such as `updateRaceHud`, reduce the first divergent CFG block before tuning allocation. Do not infer that a remaining queue, an exhausted edit budget or a higher similarity score establishes a theoretical limit.

Broad training is premature as the next intervention: the exposed failures concern missing delivery, missing compiler evidence and a testable source-lifetime mechanism. Persisting failed probes now makes subsequent training or policy selection more informative. A successful object candidate still needs separate game-build integration; this run does not establish whole-ROM completion.

Fresh `python -m eval.status` output is preserved in [research-status.txt](research-status.txt): the separate research DB remains 858 exact, reporting 718 SOLVED, 61 header-assisted, 24 reference-type-assisted and 55 recovered. These are the tool's historical assistance categories, not a clean capability count or the live campaign total.

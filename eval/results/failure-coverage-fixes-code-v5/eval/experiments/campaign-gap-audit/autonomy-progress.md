# Autonomy progress experiment (September 6, 2026)

Preselected eight functions with no attempts at cutoff 30173, two per game/SDK
and medium/large stratum. Seed, exclusions and budgets are in
`eval/results/autonomy-progress-8-v1.cohort.json`. Header-assisted; no reference
function bodies or historical candidate seeds. No integration.

## Frozen v1: operational failure, not an autonomous success

Snapshot: `eval/results/autonomy-progress-code-v1`.
Checkpoint: `eval/results/autonomy-progress-8-v1.json` (preserved unchanged).
Eight intake visits completed: two compiler successes (one frontend pass), five
compiler failures, one privileged-instruction backend blocker. No model calls
had occurred. The next recovery visit crashed in `workspace.score`: explicit
`extra=None` could not be expanded as a mapping when attaching frontend evidence.

Fix: treat absent/None logging metadata as an empty mapping, retaining frontend
and compiler recipe evidence. Six regression cases cover None/empty/populated
metadata with and without frontend results. This is an engineering intervention.
The earlier lifecycle fix defers semantic panels until a frontend-passing child
exists; two new tests cover initialization, failure retry and fixed-panel reuse.

## Frozen v2: completed bounded run

`eval/results/autonomy-progress-8-v2.json` is an explicit fork of v1, not an
untouched independent trial. Its code is separately frozen in
`eval/results/autonomy-progress-code-v2`; its cohort is unchanged.
24 visits completed, four model calls, 3,719 recorded generated tokens. No
mid-run edits or pin-change pause. Budget exhausted; no strategies declared
exhausted. No byte-exact matches or integrations.

- Two compiler/frontend passes: `findRaceItemProjectileHomingTarget` newly
  compiles after one model proposal (57/64 sampled cases pass; seven call-trace
  disagreements; unknown `__ll_mul` arity remains test-environment debt).
  `drawRaceSetupSavePlayerPanels` progresses from weighted score 85.737 to
  86.988 but still fails 64/64 sampled cases. These scores are NOT byte percentages.
- One compiling/frontend-rejected candidate: `drawMenuTextureByAssetId`.
- Four noncompiling: `__osViInit`, `drawRaceIntroFlyoverActor`, `osInitialize`,
  `__osContRamWrite`.
- One hardware-backend blocker: `__osDispatchThread` (`mtc0`/`ctc1`).

The homing-target child received semantic evaluation in the same worker that
started with a failed root: live activation evidence for DeferredPanel, not a
claim that its semantics are solved.

## Post-run scheduler correction (not in v2 results)

Repeated header-context variants changed source hashes and retriggered recovery;
`drawRaceIntroFlyoverActor` received three consecutive zero-model recovery visits
without a model visit. Bound consecutive compile-recovery visits to two, then
yield to the next eligible profile. A subsequent model visit permits recovery
of its new child. Model-disabled runs stop this churn instead of manufacturing
progress. New regression test covers both budgets and reset behavior.

Next measured targets: replay the scheduler correction; inspect candidate
`.rodata` payload/relocation grounding and stack output-buffer evidence at the
save-panel `sprintf` divergence; establish the binary-backed `__ll_mul` ABI
before treating the seven homing-target disagreements as source defects.
Do not hide either residual by weakening semantic acceptance.

Final live-worktree regression suite: 1,205 passed, 11 skipped. No workers remain
running; v2 is paused at its explicit work budget.

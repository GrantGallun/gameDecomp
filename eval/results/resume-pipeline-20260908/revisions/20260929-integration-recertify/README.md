# Amendment: re-certify integration nodes whose pinned inputs changed

Status: **APPLIED 2026-09-30** (owner: "go for it", after reviewing this proposal and the dry run). Checkpoint
35978 → 35979 (install; object_exact_or_integrated unchanged at 1,040), then the first integration-only session: 35990.
Owner authorization to prepare: 2026-09-29 ("go for it", in reply to the proposal to re-certify the stale members and
make the sweep re-verify instead of halting).

## Problem

Integration had not verified a new function since checkpoint 22879. The latest sweep before this amendment
(checkpoint 35525) ended `integration_halted`, "previously integrated union failed current preparation". Five
integrated members failed re-preparation with "certificate build input changed: …/nonmatchings/<fn>/build.sh":
calculateFixedAngleBetweenXZPoints, osSpTaskStartGo, rmonPrintf, updateRaceCameraMenuPreview,
updateRacePlayerPostUpdateAttack.

Their certificates pinned `build.sh` at sha256 `0f3bc101…` and a per-function `.compiler-<key>.json`. The files hash
`cf01a85e…` now, and the recipe files are gone (`solver.compiler_recipe.prepare` deletes superseded recipe files by
design, part of the 2026-09-17 `do`-ban removal). Every sweep re-prepares the whole integrated union and requires
each pinned input to match byte for byte, so each one halted. Separately, all 31 `function_exact_pending_integration`
nodes carried an "attempted" evidence key, so later sweeps selected nothing and returned quietly. Nothing re-certified
integrated nodes: `completion_campaign.next_profile` returns None for them, and the `recertify@` census profile covers
only `pending`.

Found 2026-09-29 while diagnosing 31 unsolved functions with 100-score attempts
(`eval/results/perfect-score-20260929/`): 11 were integrated, 17 pending integration, 2 exact outside the campaign.

## Change (one file)

`eval/campaign_integration.py` = frozen copy + `stale_inputs`, `recertify`, and a `sweep` wrapper:

- Before selection, every `integrated` / `function_exact_pending_integration` node with a changed pinned input is
  re-scored from its SAME source (sha256 checked), logged to the campaign ledger as strategy `integration-recertify`
  with the old attempt as parent.
- It is accepted only if it compiles, is object-exact or ROM-backed function-exact, passes the frontend, and the
  new certificate is not itself stale. Then the node's `attempt_id`/`verification` are replaced, and a matching
  `verified_source_bindings` entry moves to the new binding. Every attempt, success or failure, is appended to
  `integration_sweep.recertifications`.
- A failure changes nothing, so the union check halts exactly as before, but the record names the function and reason.
- Renewed nodes are returned as changed on every path, including the empty-selection early return. The checkpoint
  store rewrites only nodes named in `changed`, so an unlisted renewal would otherwise be lost on resume.

Main and frozen `campaign_integration.py` were identical before the change (diff exit 0), so reviewed = main
(sha256 `26c8b6f3…`; frozen before `37fb84d5…`).

## Evidence

- Tests: `tests/test_integration_recertify.py` (5: stale detection, renewal + binding move, refusal when no longer
  function-exact, refusal on a changed source, and the motivating fire test: sweep returns the renewed node with
  every pending node already attempted). Existing integration suites pass unchanged (52 total).
- Dry run: `dry_run.py` against copies of the state and ledger (`~/decomp/runs/integration-recertify-dryrun-20260929`),
  frozen code with only this file replaced. Result in `dry-run-result.json`, 233 s:
  - **5 of 5 stale members re-certified** (same sources, function-exact under current inputs), 0 failed.
  - **Halt cleared.** The 21-member union rebuilt `rom_exact` (isolated whole-ROM verification).
  - First new batch of 5: **`__osSpGetStatus` integrated** (verified union 21 → 22). `addEndingActorShadowRenderCallback`
    fails the full-ROM build even alone: its C declares `extern s8 gFramebufferSwapHold`, but race_flow.c has u8.
    `closeRaceRecordSettingsFlow` and `drawRaceMotionAnimationDebugViewerMotionNumber` fail the build on the same
    kind of declaration conflict (a `char[]` vs `const char[16]`), and also fail the frontend policy check.
    `drawEndingObjectSpriteDebugViewer` is blocked in preparation ("needs shared declaration integration").
  - Six nodes returned as changed (5 renewed + 1 integrated), all persisted by `save(changed=…)`.
  - The copied checkpoint held 3 jobs interrupted by the 2026-09-29 WSL shutdown. The dry run dropped them in memory
    only; the live controller recovered them before its first sweep (below).

## Applied (2026-09-30)

1. GPU check (6 driver resets in the prior 12 h; owner confirmed not gaming). Both pause markers set.
2. Drained the 3 interrupted jobs with the recorded launch command + `--resume` while paused. All three were
   evaluated and imported normally (2 `schema_patch` model jobs, 1 `operand_repair`); 0 driver resets.
3. `stage.py` at checkpoint 35978: 3,326 unchanged pins verified, 0 drifted imports. `verify_stage.py`: 52 passed
   on the copied frozen tree. `apply_amendment.py --apply`: checkpoint 35979, object_exact_or_integrated 1,040
   unchanged, no rollback.
4. Pause markers removed. One integration-only controller session (launch command, `--max-work-items 0`):
   **5 of 5 re-certified; sweep `rom_exact`; verified union 22; `__osSpGetStatus` integrated;
   object_exact_or_integrated 1,040 → 1,041**, the first new integration since checkpoint 22879. Same outcome as the dry run.
5. Further integration-only sessions until a sweep selects nothing new: see `sessions` below once recorded.

## Follow-ups (not in this amendment)

- Declaration conflicts at integration: a candidate's local `extern` type differs from the translation unit's.
  The function is byte-exact with its own type, so integrating it means reconciling the declaration while keeping
  the codegen (a cast at the use). A small class of its own.
- `drawEndingObjectSpriteDebugViewer`: shared declaration integration, deliberately not attempted by the preparer.

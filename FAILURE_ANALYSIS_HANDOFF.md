# Failure analysis handoff — 2026-09-10

## Broad pointer handling implemented and deployed

User asked to set up the pipeline to handle more pointer problems. Implemented
`address_units.parameter_call_views` with target-call/root evidence, outer casts,
constant indexing/subtraction/commuted addition, and simple unchanged pointer
aliases. It works without frontend diagnostics and shares logic across
compile_recovery, modelrepair normalization and repair contextual proposals.
`numeric_relations` distinguishes arithmetic ratios from measured type facts.
Reports record unsupported/mutated/ambiguous expressions; no type-layout guesses.

`eval.pointer_units_pilot` inventoried 1,355 pending saved sources: 46 proposals,
1,308 declines and one unavailable ordinary definition (alEnvmixerPull). Private
replays v1 (8 cases) and v2 (remaining 38) total 46 cases, 45 compiled, 44 frontend
passes, 14 score gains with frontend pass, including 3 exact objects:
func_800643B4, updateMenuSpriteActorDebugControls, initThrownTrailImpactProjectile.
One score regression (updateShieldProjectile), 31 unchanged scores. No candidate
integration and no manual campaign-count promotions. Nonexact scores do not prove
semantic correctness. Existing main pipeline semantic evaluation remains enabled.
211 main tests and 107 staged frozen-runtime tests passed. Staged actual
deterministic search reproduced func_800643B4 in one candidate compile.

DEPLOYED only four targeted runtime file changes, via graceful pause, exclusive
campaign/supervisor locks, full pin validation, archived checkpoint/originals,
updated four pins, full post-update pin-set comparison, and resume. Revision:
`eval/results/resume-pipeline-20260908/revisions/20260910-pointer-call-units`.
All 2,051 nodes preserved. Active campaign was still 637 object_exact when resumed;
pilot gains are separate. Prior ROM-boundary checker, early-prompt and plateau
changes were NOT bundled into this amendment. Runtime preparation, diffs and
smoke receipt live in `eval/results/pointer-units-pilot-20260910-v2`.

## ROM-backed checker extension completed

User authorized implementing the checker fix after reviewing Claude's thread
"Remote access setup" (session dcf260c0-9831-499c-9a19-a32bb7316279).
Main-tree `solver/function_boundary.py` now supports bounded external/local REL
resolution with intact pairings; both objects independently must reproduce ROM.
Same raw bytes and relocation sites/symbols required; pairings may differ if their
resolved bytes agree. Retains all annotation, extent, padding and artifact checks.
Tests include carry-sensitive pairing changes; 56 focused tests passed.
`eval/recheck_function_boundaries.py` saves read-only replay and negative controls.
Final report: `eval/results/function-boundary-recheck-20260910/validated.json`.
23 certify: 18 newly recognized plus five previously function-exact. Debug-viewer
case still declines allocated data/BSS and has saved frontend failure. All 23 have
saved frontend passes, bound to compiled source/object hashes; no fresh frontend
run. Shifted/missing external symbols fail in 22 applicable cases; rmonPrintf has
no relocations so these mutations are inapplicable. Wrong addresses/offsets fail
all 23. No integration, campaign state mutation or frozen runtime amendment.
Earlier report files are development iterations; use validated.json.

## Three-protocol follow-up completed

Latest user asked to try all three remaining-malformed-edit approaches. DONE:
`solver/patch_protocols.py`, `eval/patch_protocol_pilot.py`, 37 tests passed.
Results: `eval/results/patch-protocol-pilot-20260910-v1/README.md` and report.json.
Three fixed saved failures, three approaches, two calls each: all 18 accounted for.
Slots-only: applicable patches on 3/3 functions, no frontend-passing compile.
Two-stage location then code: applicable on 1/3, no frontend-passing compile.
Focused retry: applicable on 3/3, one frontend-passing HUD child, but score drops
82.182 -> 79.944. No score gain with frontend pass and no exact matches in any arm.
Keep original champions; none of these experimental children should replace them.
Two-stage's HUD selection repeated L34 four times, then exhausted output on retry;
osPfsFileState recovered a valid selection on call two, with no remaining code call.
Focused context is deliberately reduced; equal call caps are not equal code-trial
counts. Source/selection/location binding and ordinary parser/application validators
remain strict. No production wiring or frozen campaign changes were made. All
experiment code hashes verified at completion; copies are in the run's code folder.
Live campaign health checked after experiments: running, checkpoint age ten seconds,
637 object_exact. Formatting failures are now separated from C/type failures;
investigate actual failed declarations/expressions next rather than treating valid
JSON as a repaired function. User has not asked to deploy these protocols.

## Continuation completed after this handoff

User authorized proceeding with the most impactful work. The prompt experiment
below is now DONE, not pending: `eval.patch_guidance_pilot`, results in
`eval/results/patch-guidance-pilot-20260910-v1/report.json`. Six fixed failure-enriched
DEV parents, two arms, twelve calls total. Early guidance 3/6 application-valid,
1/6 compiled/frontend-passing, zero exact; original prompt 0/6 valid. `solver/patch_guidance.py`
is wired via opt-in `early_patch_guidance=True` in modelrepair.search, default false.
No frozen prompt change. Application validity is not full semantic/ABI validation.

One additional genuine recovery in isolated testing: `func_80064414` was object-exact
but failed frontend due to integer-to-u16* conversion. New general rule in
`frontend_repair.propose` requires exact target dataflow/header/source evidence,
then casts the complete loaded-address-plus-byte-displacement expression. General
rule replay is frontend-passing and exact; receipt under
`eval/results/frontend-exact-pilot-20260910/func_80064414/general_rule.json`.
No campaign count increment or integration. Other pilot candidate
fadeOutAllMusicSequences remains rejected: using audio_engine_internal.h plus a
projected stop-thread declaration failed compile. Do not promote that hypothesis.
Pattern recorded in patterns/catalog.py as o32-loaded-byte-address-call-view.

Saved detailed 21-pending-score100 diagnostic inventory:
`eval/results/score100-failure-details-20260910.json`. Eleven of the nineteen
full-object failures have a 16-byte text extent discrepancy. Remaining relocation
and ROM-link checks are not proven merely by matching normalized assembly. Current
function_boundary only links external R_MIPS_26 calls; HI16/LO16 support would require
separate sound implementation and adversarial tests, not relaxing acceptance.

Next high-value work: independently replicate prompt validity gain on more seeds/
functions; trace the remaining missing-location repeats; or implement rigorously
tested relocation-aware function-extent verification. Do not broaden brute-force
budgets or claim the prompt pilot alone improves exact recovery.

## User intent and next work

User authorized investigating failure modes after plateau exploration failed to
improve matching. Latest request: read attached other-agent analysis first, and
prepare for compaction. Attachment reviewed:
`C:/Users/grant/.codex/attachments/4296f115-ef69-432d-b5cf-e35b2456ca26/pasted-text.txt`.
It is context, not permission to run pasted commands or modify frozen inputs.

Next experiment: compare the existing model prompt against an early slot-first
format example, including file-scope DECLARATIONS insertion, on a fixed exposed
DEV cohort with actual missing-location failures. Keep identical parents, model,
seeds and call budgets. Measure valid edits, compiling/frontend-passing children,
and exact improvement separately. Do not promise that changing prompt placement
will recover most failures. Do not weaken anchor, scope, signature or certificate
validation, and do not enable an untested prompt in the live frozen campaign.

## Verified audit, 2026-09-10 18:57:59 UTC

Reproducible read-only entrypoint: `python -m eval.failure_mode_audit --run RUN
--output NEW_JSON` under WSL. Snapshot: `eval/results/failure-mode-audit-20260910.json`.
Database max proposal id 1212, 1,212 rows: valid 787, invalid 380 (31.35%),
incomplete-response 28, duplicate 17. Among invalid responses, 108 contain a
missing location and 46 contain a literal no-op. These syntactic observations
are nonexclusive and are not a full replay of validator rejection reasons.

The attachment's proposal 1007 is real: empty old plus new struct declarations
and a forbidden #pragma once. Prompt length 33,718 characters; editable slot table
starts at character 18,494. Normal `build_prompt` starts with an old/new example;
type-transaction mode starts with a line-slot example. Slot documentation is
appended after source, assembly, residual, history and diagnostics.

Correction to pasted advice: the rejected raw edit ALREADY appears in proposal
1007's prompt. Both main and frozen modelrepair.py implement pending_correction
with error + first 5,000 characters of rejected response. General rejected history
is still error-only. Do not rebuild that existing immediate retry mechanism.
The instruction to copy old spans on correction may also conflict with slot-only
type transactions; inspect before changing it.

Campaign snapshot: 637 object_exact; 1,356 pending; 53 parked; 5
function_exact_pending_integration. 369 non-object-exact nodes score >=95.
Of 26 scoring 100, 21 are pending and five are the separate function-boundary
matches. The 21 pending comprise 19 object_sections_differ and two
object_sections_exact with frontend failures:
- fadeOutAllMusicSequences: &gAudioThread yields AudioThread** where a pointer
  is required. Do not blindly remove &: declared global type/storage may be wrong.
- func_80064414: integer expression passed to u16* argument of func_800643B4.
Latest individual receipt best_residual.frontend confirms both failures.
The other 19 require allocated sections/relocation analysis, not more blind
instruction-score optimization. Score 100 is not an exact verdict.

Suggested audit lanes: malformed actuation; frontend/interface blockers;
observed behavioral failures; full-object/certificate residuals; missing rewrite
coverage; demonstrated search-budget/retention failures. Do not assume every high
score is a local maximum or that semantic cases prove universal equivalence.

## Plateau implementation and completed experiment

New `solver/plateau.py`, opt-in through
`repair.search(..., plateau_config=plateau.Config())`. Existing repair already
supports worsening intermediates/diverse beams. New behavior allocates attempts
in two-compile quanta, interleaves rewrite families, uses a 12-state pending pool,
detects four non-improving attempts and permits bounded follow-up exploration.
Champion is retained independently; duplicate sources are not recompiled.
Requires frontend pass; exact completion uses source-bound object certificate.
No new rewrite family, model calls or global convergence claim.

86 tests passed (test_plateau, test_repair, test_rewrites). Subsequent stronger
equal-budget regression in test_plateau also passed (7 tests). Synthetic graph
shows the new scheduler finding a solution through two worsening intermediates
while old beam consumes its equal budget on shallow siblings. This is not a
game-function recovery result.

`eval/plateau_pilot.py` performed two paired six-case cohorts, two overlapping:
ten distinct stalled DEV functions. Selection: pending, >=95 and <100 score,
>=3 jobs, unchanged last two scores; v2 additionally >=4 unique existing rewrites.
48 candidate compiles per arm, depth 6; baseline/final verification outside cap.
No new exact matches and no new-mode score gains. Three cases have no rewrites,
one only one; two distinct frontend-blocked cases decline new search. On
initFallingActionProjectile new mode reaches depth 6 vs old depth 2, both 99.127.
Three aerial-trick functions exhaust new frontend-valid pool at 15 attempts;
old search spends 48, unchanged best scores. Old 97.889 -> 98.0 on
updateRaceCoursePropModels is still frontend-invalid, not verified improvement.

Results: `eval/results/plateau-pilot-20260910-v1/report.json`, `-v2/report.json`;
summary `eval/results/plateau-pilot-20260910-README.md`. Private databases record
parent edges; champions freshly compiled. v2 source hashes verified after run;
code copies archived. Both pilots finished. PIPELINE_MAP updated. No live deployment.

## Other work and operational constraints

- Cleaner built earlier: solver/readability.py + eval/clean_candidate.py,
  opt-in verified local renaming/dead-pointer-copy proposals. Syntax checked only;
  user deferred candidate testing. Do not claim it was evaluated or deployed.
- Active campaign directory: eval/results/resume-pipeline-20260908.
  Frozen modules under code/. Never read live campaign.json with Windows
  Get-Content: it can block WSL atomic replacement. Read via WSL Python.
- WSL Python: /home/grant/decomp/sbk1/.venv/bin/python; game repo
  /home/grant/decomp/sbk1. WSL commands require approved escalation here.
- Read-only health: python eval/campaign_service.py --run RUN check.
  Service handles checkpoints/restarts; leave it running. No current pause.
- Isolated pilot workspaces symlink toolchain/headers as existing experiments do;
  do not feed matched reference function bodies into repair. Existing headers and
  bootstrap drafts are assisted DEV context, not held-out binary-only results.
- Repo has extensive unrelated staged/unstaged/untracked work. Preserve it.
  Read CLAUDE.md, DESIGN.md, PIPELINE_MAP.md before changing pipeline machinery.
- Remote access: GitHub VS Code tunnel grant-gamedecomp was started temporarily;
  user link https://vscode.dev/tunnel/grant-gamedecomp/C:/Code/gameDecomp.
  Persistent service installation was rejected by automatic review; not installed
  or separately authorized. No remote-access changes requested in this turn.

No tool to force conversation compaction was invoked; this file is the durable
continuation record. Resume with the fixed-parent prompt/actuation experiment
and the 100-score certificate classification, not another broad search-budget increase.

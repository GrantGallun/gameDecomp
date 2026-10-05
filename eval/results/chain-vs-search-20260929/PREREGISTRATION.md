# Does the site-edit search reach what the greedy chain reaches?

PRE-REGISTRATION, written 2026-09-29 before any run.

The greedy chain (`../temp-copyback-20260929/greedy_chain.py`) made osMotorStart and osMotorStop exact from their
raw drafts using `unaligned_copy`, `temp_copyback` and `counted_loop`. Earlier search arms found 0 exact, but they ran
before `counted_loop` and the named-field fix existed, so "the search can't compose them" has not been measured.

Frame: the 19 greedy-chain functions (`frame.json`), each from its best compiling non-exact ledger attempt.
Current main-tree code. Two arms, budget 48, depth 3, per_step 24, beam 3, no model:
- **default**: `site_edits.search(score, source, function)`, which is what `eval/site_edit_repair.run` would call.
- **class**: `key=residual_classes.key, focus=residual_classes.focus`.
Logged to `/home/grant/decomp/runs/chain-vs-search-20260929/{default,class}.sqlite`.

Predictions:
- Both arms reach osMotorStart and osMotorStop exact (three edits fit in depth 3; shape edits are ranked first).
- For every other function, each arm's best score is ≥ the chain's minus 1 point.
- If an arm misses an exact the chain found, read that function's trail before changing anything.

## Result of the first run, and amendment 1 (before any lane run)

Both arms: 0 exact; osMotorStart/Stop 98.971 in both (prediction failed). Trail (default arm, osMotorStart):
level 0 spent 24 compiles, 4 of them shape edits (copy 93.8); level 1 spent the other 24, 20 of them on typed
edits that left the score unchanged, and reached loop→copy at instruction/register distance (7, 0). The budget
(48) = 2 × per_step (24), so **depth 3 is unreachable by construction**, and the third edit (copy-back) was never
tried.

Change: shape edits become a priority lane in `site_edits.propose` (test
`test_shape_repairs_are_a_priority_lane`). Three arms, default key:
- **lane48**: budget 48, per_step 24. Prediction: still misses the exacts (no level 2).
- **lane16**: budget 48, per_step 16. Prediction: osMotorStart/Stop exact.
- **lane72**: budget 72, per_step 24. Prediction: osMotorStart/Stop exact; others ≥ lane16.

## Lane results and amendment 2 (before the frame-A check)

lane48 0 exact (770 compiles); lane16 2 exact (osMotorStart, osMotorStop; 668); lane72 2 exact (1012). All three
predictions held. lane16 trails lane48 on best score for alFxPull (76.5 vs 78.8) and
updateCharacterSelectUnlockedCourseList (88.6 vs 90.1).

Before `per_step=16` becomes the default: regression check on frame A (`../composed-edits-20260929/frame-A.json`,
77 small near-misses, 14 exact under per_step 24). Prediction: lane16 ≥ 14 exact, the same 14 included. If fewer,
the default stays and lane16 remains an option.

## Amendment 3: lane16 failed frame A (13, lost clearRaceReplayCourseGrid); checking lane72 (before run)

lane72 keeps per_step 24 and adds a reachable third level. Prediction: ≥ 14 on frame A with the same 14.

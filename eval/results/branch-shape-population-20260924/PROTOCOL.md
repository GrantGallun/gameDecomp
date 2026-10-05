# Paired population trial: solver.branch_shape (select_else, split_merge, o1_register_local, empty_then_return)

Written 2026-09-24, before any compile in this directory. Rules: eval/results/branch-layout-20260924/PROTOCOL.md
(H1' path count, H2, H3', the P5a adjacent-reuse case) and the fire tests there (`fire_module.py`: 18 functions got
a proposal, 17 improved, drawTrainingCourseLessonEndMenu 98.093 -> 100.0 in three greedy rounds).

Code: code-v10 = code-v9 (the locality run's frozen code) + overlays `solver/regalloc_mutations.py` (the four
families wired after `evidence_site`) and `solver/branch_shape.py`. Nothing else changes. (The working copy's
`frontend_type_repair.py` differs from code-v9 and is deliberately not overlaid.)

Two runs, same scheduler, depth and budget (32) as the restart rounds:
1. **Loss check** (install rule): the 224 frozen population sources, code-v10, paired against the locality run
   (code-v9, same sources, same budget; rows in ~/decomp/experiments/locality-population-20260923).
2. **Gain measure**: round 4 from the round-3 best nodes of the still-unsolved functions (`starts.py` on the
   round-3 rows), both arms: control code-v9, treatment code-v10.

Acceptance, fixed now: install (leave the families wired in the working copy) only if run 1 loses 0 exacts against
the locality run. Report run 2's exact gains and losses, the paired score change on functions open in both arms,
and each family's machinery card (applications, improve, exact, compile failures). Any new exact is confirmed by
the run's independent recompile, then recorded with a ratchet check, then eval.status is regenerated.
Prediction (not a criterion): run 2 treatment gains drawTrainingCourseLessonEndMenu; run 1 changes nothing on the
functions it already solved, because the families only fire on diffs with more `b`/`j` or `slt*` in the target.

## Run 1 result (loss check, `analysis.json`)
224/224 rows, no worker errors, no receipt errors. code-v10 16 exact, locality (code-v9) 16 exact: **0 losses, 0
gains**. The acceptance rule is met. Open in both: 14 improved, 1 worse (updateCloseRangeHomingItemProjectile 79.1 ->
78.2), mean +1.0. The -O1 io functions went 62.6-67.6 -> 97.7-98.3. drawTrainingCourseLessonEndMenu reached 98.093 from
the frozen source, the same as round 3's best, so the exact is expected only from the round-3 start (run 2).
Event: the WSL VM restarted once during run 1 (about 00:36; cause not identified; the hourly maintenance task last ran at
23:55 and does not shut WSL down). launch.sh restarted, finished rows were read back from their atomic files, and
in-flight functions restarted from scratch. Since the search is deterministic, the rows are unaffected.

## Run 2 result (round 4 from the round-3 bests, `analysis.json` run2_round4)
206 functions, control 0 exact, treatment 0 exact (no gains, no losses). Open in both: 16 improved, 0 worse, mean
+1.04. Family cards (treatment): select_else 15 edges / 7 functions, 80% of acted improved; split_merge 15 / 6, 58%;
o1_register_local 8 / 4, 100%; empty_then_return 3 / 3, 100%; none broke the build.
Prediction missed: drawTrainingCourseLessonEndMenu stopped at 99.683 at depth 2 when the budget ran out, one select
short of the 100.0 chain the fire test found. Its first 26 compiles went to other families and to `split_merge` groups
in alphabetical order. Follow-up with ordering fixes: eval/results/branch-shape-v11-20260924 (code-v12).
**Decision:** acceptance is met (run 1 lost 0 exacts), so the four families stay wired in the working copy.

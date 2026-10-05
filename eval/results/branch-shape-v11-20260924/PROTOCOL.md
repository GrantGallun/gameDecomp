# Paired population trial: branch_shape v11 (+ m2c_struct_copy, dup_return_merge)

Written 2026-09-24, before any compile in this directory, while the code-v10 trial
(eval/results/branch-shape-population-20260924) was running.

Code: code-v11 = code-v10 + the working copy's `solver/branch_shape.py`, which adds `m2c_struct_copy` (E4, fire test
copyGfxCommandBlockToScratch 42.0 -> 100.0) and `dup_return_merge` (H3', fire test __MusIntFindChannel 90.9 -> 100.0).
Nothing else changes.

Runs (same scheduler, depth and budget 32):
1. Loss check: the 224 frozen population sources, code-v11, paired against the code-v10 run 1 rows and the locality
   rows.
2. Round 4 treatment-v11 from the same round-3 starts (`starts-round4.json` of the v10 trial), paired against that
   trial's round-4 control and treatment.

Acceptance, fixed now: keep the two families wired only if run 1 loses 0 exacts against both code-v10 and locality.
New exacts: independent recompile (in the run), then `record.py` with a ratchet check, then eval.status.

## Amendment A1 (2026-09-24, before any compile in this directory)
The code-v10 trial's round 4 ended 0/0 exact. drawTrainingCourseLessonEndMenu reached 99.683 at depth 2 and ran out of
budget one select short; its first 26 compiles went to other families and to `split_merge` groups taken in
alphabetical order (s0 before s1), although the diff's `-slti at,s1,...` names s1. Two ordering changes in
`branch_shape.py`, no new compiler claim: `select_else` yields all sites together first; `split_merge` proposes first
the groups whose register the target's `slt` lines use. code-v11 (frozen before this, never run) is left unused
(`freeze-unused-code-v11.json`). This trial now freezes **code-v12** = code-v10 + the amended `branch_shape.py`
(six families). The arms, acceptance and run names are otherwise unchanged; "v11" in run names means this code.

## Result (code-v12, `analysis.json`)
- Run 1 (224 frozen sources): 18 exact against 16 for both locality and code-v10, **0 losses**; gains __MusIntFindChannel
  (dup_return_merge, then frontend_type) and copyGfxCommandBlockToScratch (m2c_struct_copy). Open in both vs code-v10: 2
  improved, 1 worse (drawCourseSelectPlayerPanels 90.2 -> 88.4: `select_else:all` used budget that single sites had
  used better).
- Run 2 (round 4, 206 functions): **3 exact against 0** for both control and code-v10: __MusIntFindChannel,
  copyGfxCommandBlockToScratch, drawTrainingCourseLessonEndMenu (split_merge -> select_else). All three were confirmed
  by the run's independent recompile. No losses.
- Recorded (`../branch-shape-population-20260924/record.py`, ratchet-checked): receipts 95887-95889, **378 -> 381**.
  copyGfxCommandBlockToScratch is source-independent (SOLVED 274 -> 275). The other two include a reconstructed
  `game/` header (header-assisted 23 -> 25).
- **Decision:** acceptance met; all six families stay wired (the working copy's branch_shape.py equals code-v12's).

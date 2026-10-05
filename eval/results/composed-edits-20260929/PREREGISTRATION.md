# Composed edits: branch-shape repairs inside the site-edit search, families interleaved

PRE-REGISTRATION, written 2026-09-29 before any run.

## Change under test (main tree, `solver/site_edits.py`)

1. `_shape_edits`: `branch_shape.variants` enter `propose` as `shape:*` edits ranked first, without
   needing an attributed line. Motivation: `../branch-routing-20260929/` (52 of 75 improved, 0 broke,
   202 of 203 variants never compiled because only register search called them).
2. `_interleaved`: round-robin across edit families instead of a strict family order. Motivation:
   `../width-edits-20260929/`, where 85 functions had width edits proposed and none compiled within
   the 24-compile level.

Search parameters unchanged (budget 48, depth 3, per_step 24, beam 3), and so is the acceptance rule.

## Frames

- **A, regression:** the 77-function frame of `../site-edits-20260929/`, same retained campaign
  attempts. The old search made 4 exact with site edits alone.
- **B, composition:** 319 functions, the union of the width frame (299) and the branch-routing
  frame (75), with 55 in both. Each at its best compiling non-exact attempt in either ledger.

Logged to `/home/grant/decomp/runs/composed-edits-20260929/trial.sqlite`. No model, no reference source.

## Predictions

- A: ≥ 4 exact. Fewer is a regression, and the change is not kept as is.
- B: 2–10 exact. 0 means composition inside one depth-3 beam still doesn't reach finished
  functions, and the remaining blocker is the acceptance rule (width: 56 of 106 class-correct edits
  raise instruction distance) or depth.
- B fire: width edits compiled in ≥ 85% of the 299 width functions (was 72%); branch-shape edits
  compiled in ≥ 95% of the 75 branch functions.

## Amendment 1 (2026-09-29, after frame A, before frame B finished and before any control run)

Frame A gave 14 exact, against 4 and 6 in the earlier site-edit rounds. `solver/site_edits.py` has
changed since those rounds for reasons other than this change, so that comparison doesn't
attribute anything. Added a paired **control arm** (`control.py` → `run_control.py`): the same
current code with `_shape_edits` returning nothing and `_interleaved` the identity, same frames, same
budget, logged to `control.sqlite`. The change is credited only with treatment-minus-control on
the same functions. Prediction: treatment exceeds control on A by 1–6 exacts, and on B by 0–8.

## Amendment 2 (2026-09-29): class-by-class ordering, before any class-key run

The user asked for repair class by class. Frame B was stopped at 207 of 319 (composed arm: 0 exact,
width edits fired in 204 of 207, best score improved in 71). The control arm was not run.

**New arm, class key** (`run_class.py`): identical to the composed arm except that
`site_edits.search(..., key=solver.residual_classes.key)` orders children lexicographically by class,
upstream first: control-flow inventory, one-sided extensions, layout, operand (immediate + relocation),
instruction distance, register distance. Same budget 48, depth 3, per_step 24, beam 3. Logged to
`class.sqlite`.

Frames: A (77, regression) and B207 (`frame-B207.json`, the 207 B functions the composed arm finished),
so each is paired with the composed arm.

Predictions:
- A: ≥ 12 exact (composed arm 14). Fewer means ordering by class loses the near-miss finishes that total
  distance found, and the key is not kept as the default.
- B207: 1–8 exact (composed 0). Class progress: functions whose best state has a lower control-flow or
  width count than the composed arm's best, in ≥ 30%.
- If B207 is 0 again: the blocker is budget/depth (six classes cannot each take a step in a depth-3
  beam), and the next arm raises depth, not the rule.

## Amendment 3 (2026-09-29): class-focused localisation, before any focus run

Class-key results (`compare.py`, `stages.py`). A: 14 exact, the same 14 as composed. B207: 0 exact in
both arms. Class key was lower on control_flow+width in 25 functions vs 2, and better on the full key
in 37 vs 12. The ≥ 30% class-progress prediction failed (12%). At the best state 145 of 207 searches
were still on **width** and 45 on control flow, and 145 used the whole budget. The searches stall
on the first class, not on depth. The heaviest four lines are knock-on, so the class's own lines are
never proposed.

**New arm, class + focus** (`run_focus.py`): class key, plus `focus=residual_classes.focus`. At each
parent, `site_edits.propose(only=...)` ranks the lines that carry the first wrong class
(branch/jump rows for control flow, extension rows for width) ahead of the heaviest lines. Same budget
and depth. Logged to `focus.sqlite`.

Predictions (paired with the class arm on the same functions):
- A: ≥ 13 exact.
- B207: stage stalls on width fall from 145 to ≤ 110. Functions with lower control_flow+width than
  the class arm: ≥ 40. Exact: 0–5. Exact stays likely 0, because 145 functions also exhausted
  budget and later classes remain.

## Amendment 4 (2026-09-29): width counter bug; rerun of the focus arm, before any rerun

Focus arm (broken counter): A 14 exact (same 14); B207 0 exact. It was lower on control_flow+width
than the class arm in 53 vs 12 (≥ 40 held), and better on the full key in 63 vs 17. Width stalls were
143 (≤ 110 failed).

Reading small stalled functions (`width_sample.py`) showed the width counter was wrong. It counted
every extension inside a differing diff region, so the *same* extension renamed or moved (Fwave:
`andi t1,v1,0xffff` vs `andi t0,…`) scored as a width fault, and the class key sent the search after a
register difference. Fixed (`residual_classes.width` compares extension kinds with registers blanked;
test `test_renamed_or_moved_extension_is_not_a_width_fault`). Re-measured offline: 80 of 207 baselines
have a real width fault, not 133.

The class and focus arms were steered by the broken counter, so their stage results aren't evidence
about class-by-class repair. **Rerun (`focus2`)**: class key + focus with the corrected counter, same
frames and budget, logged to `focus2.sqlite`.

Predictions:
- A: ≥ 13 exact.
- B207: best-state width stalls ≤ 55 (of 80 real). Later-stage functions (layout/operand/instructions/
  registers at best) > 91 (the focus arm's, re-measured). Exact 0–6.

## Amendment 5 (2026-09-29): unaligned-copy class, before the run

New class found by reading width stalls: byte-packed words where the target uses `lwl/lwr`. Rule confirmed
on IDO (catalog `ido53-unaligned-struct-copy`); `solver/unaligned_copy.py` wired into `site_edits` as a
`shape:unaligned_copy` edit. Single-compile fire run (`../unaligned-copy-20260929/`): 6 of 8 fire, e.g.
osMotorStart 50.6 → 89.3. **Arm `ucopy`**: focus2 settings (class key + focus) from the 8 functions' best
states, logged to `ucopy.sqlite`. Prediction: 0–2 exact. Each fired function's best state moves past
width/control flow into layout/operand/registers, and the best score is ≥ the single-compile result.

## Amendment 6 (2026-09-29): deeper search on the copy class, before the run

`ucopy` result: 0/8 exact; best states match the single-compile results. Reading osMotorStart's residual:
the copy is right; left is the frame (0x50 target, 0x70 → 0x60 after `_drop_orphans`), from m2c temporaries
homed at -O1 (`temp_t5 = sp4C + 1`) and a type edit that pulled in an `s0` save. Temporary inlining has
existing owners (rewrite pool `inline_temporary`). **Arm `ucopy-deep`**: same as `ucopy` with budget
160 and depth 6, on the 6 functions where the copy fires. Prediction: 0–2 exact. If 0, read the stall
class of each best state before changing anything else.

## Amendment 7 (2026-09-29): copy-back temporaries, before the run

`ucopy-deep`: 0/6 exact, stopped early. osMotorStart's deep search tried only decl/literal/declorder/type/uncast
edits: no temporary-inlining edit was ever proposed. (My claim that strict class order blocked later
classes was wrong: lexicographic order accepts a child equal on the stuck class and better later.) New
class and owner: `solver/temp_copyback.py` (catalog `ido53-o1-copyback-temporary`, IDO -O1 probe in
`../temp-copyback-20260929/`), wired as `shape:temp_copyback`. Single chain (`chain.py`): osMotorStart
89.3 → 92.1, __osContRamRead 80.5 → 83.3, __osContRamWrite 84.2 → 85.5. **Arm `ucopy2`**: the `ucopy` settings (budget 48) on
the 6 copy functions with both edits available. Prediction: best ≥ chain result in each; 0–1 exact.

## Amendment 8 (2026-09-29): frame class, before the rerun

`ucopy2`: osMotorStart's best 87.6, under the chain's 92.1. The merge was compiled (92.092, key
[0,1,2,3,74,6]) and rejected against its parent [0,1,2,2,76,22] for one more operand fault. That
fault came from stack-relative constants moved by the frame. Fix: `frame` becomes its own class
(after width; bytes/8 of frame-size difference), and `operand` excludes constants of instructions that
use `sp`. Tests: `test_stack_relative_constants_are_frame_not_operand`,
`test_merge_that_only_moves_frame_offsets_ranks_ahead`. **Arm `ucopy3`**: same as ucopy2 with the new key.
Prediction: osMotorStart/Stop best ≥ 92.0; others ≥ their chain result; 0–1 exact.

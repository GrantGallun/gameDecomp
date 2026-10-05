# Composed and class-by-class site edits — results so far

Pre-registration with amendments 1–4: `PREREGISTRATION.md`. All arms: budget 48, depth 3, per_step 24,
beam 3. No model, no reference source. Attempts in `/home/grant/decomp/runs/composed-edits-20260929/*.sqlite`.

## Arms

| arm | change | A exact (77) | B207 exact |
|---|---|---:|---:|
| composed (`run.py`) | branch-shape edits in the site search + family interleaving | 14 | 0 |
| class (`run_class.py`) | + `residual_classes.key` (fix upstream classes first) | 14 | 0 |
| focus (`run_focus.py`) | + class-focused localisation (**broken width counter**) | 14 | 0 |
| focus2 (`run_focus2.py`) | same, corrected counter | 14 | 0 |

The same 14 functions every time. 11 of them were already exact in the campaign ledger, and all but
two in the earlier site-edit trial database. Frame A is a regression check, not a yield number.
The control arm (amendment 1) was never run, so the composed change's own contribution on A is
unattributed.

## What each step changed on B207 (paired, `compare.py`)

- composed vs width-only search: width edits fire in 204 of 207 (was 72%); best score improved in 71.
- class vs composed: lower control_flow+width in 25 vs 2; better full class key in 37 vs 12.
- focus vs class: lower control_flow+width in 53 vs 12; better full key in 63 vs 17.
- focus2 vs focus: nearly identical (2 vs 1, 16 vs 10).

## The width counter bug (amendment 4)

`residual_classes.width` first counted any extension inside a differing diff region, so a renamed or
moved extension scored as a width fault. Found by reading stalled functions (`width_sample.py`: Fwave,
__osSumcalc). Fixed to compare extension kinds with registers blanked, with a regression test.
Real width faults at baseline: 80 of 207, not 133.

## Where the corrected search stalls (`stages.py focus2`)

First class still wrong at each function's best state, and why the search stopped:

| stage | budget exhausted | no improving child | total |
|---|---:|---:|---:|
| width | 44 | 27 | 71 |
| control_flow | 26 | 18 | 44 |
| layout | 31 | 12 | 43 |
| operand | 20 | 15 | 35 |
| instructions | 8 | 4 | 12 |
| registers | 0 | 2 | 2 |

92 of 207 functions reached control_flow = 0 and width = 0 in some attempt. None became exact.

## Reading

Class-by-class ordering moves functions through the upstream classes, and reached 92 with both
upstream classes clean. It stalls for two measured reasons in roughly 60/40 proportion:

1. **Budget** (129 of 207): 48 compiles over six classes. Next arm: per-stage budget (e.g. 24 compiles per
   class stage) or depth 6, predicted before running.
2. **Vocabulary** (78 of 207: no child improved the class): the site-edit vocabulary has no edit
   that reduces that class at the chosen lines. For layout, the owner is `solver/diffrepair.py`
   (diff-driven struct repair), which isn't in this search. For width, the 27 no-improvement cases are
   the next thing to read by hand, as Fwave was.

Not yet done: control arm, PIPELINE_MAP entry for the main-tree wiring (after the next arm decides
whether the class key becomes a default), campaign amendment (none; main tree only).

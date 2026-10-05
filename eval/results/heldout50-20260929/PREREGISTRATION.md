# Held-out test of today's site-edit package on 50 unseen functions

PRE-REGISTRATION, written 2026-09-29 before the frame was drawn or any compile. User's criterion: test on ~50
functions; if positive and significant, the mechanism counts as mature and work moves on, otherwise it continues.

## Frame

50 functions drawn uniformly (`random.Random(20260929)`) from unsolved functions (no exact attempt in either
ledger) that have a compiling attempt, **excluding every function used today for development or tuning**: the
19 greedy-chain functions, the 77 of frame A, and the 319 of frame B (`make_frame.py`). Each starts from its best
compiling non-exact attempt in either ledger.

## Arms (same functions, same starting source, no model, no reference source)

- **treatment**: current main-tree defaults: `site_edits.search(score, source, function)` with shape edits
  (branch_shape, unaligned_copy, temp_copyback, counted_loop) as a priority lane, interleaved families, budget 72.
- **control**: this morning's site-edit search: `_shape_edits` returns nothing, `_interleaved` is the identity (strict
  family order), budget 48 (`control.py`).
Logged to `/home/grant/decomp/runs/heldout50-20260929/{treatment,control}.sqlite`.

## Primary measure and decision

Per function, compare the two arms' best states by `site_edits.gradient` (compiled, instruction distance, register
distance; lower is better). Count treatment wins and losses, dropping ties. **Positive and significant** means wins
> losses with one-sided exact binomial (sign test) p < 0.05 on the non-tied pairs. If so, the package is treated
as mature (population-measured, 4/5) and work moves to the next mechanism. Otherwise work on it continues.

Secondary, reported without a decision rule: exact functions per arm, mean best-score difference, compiles per arm,
and which shape families appear on treatment's improving paths.

## Predictions

- Ties dominate: most functions have no shape-edit signature (the loop generator fired on 18 of 843 unsolved; the copy
  class is 8 functions), so both arms run the same typed edits.
- Among non-ties, treatment wins more (priority lane plus a reachable third level). Predicted wins:losses ≈ 12:4,
  significant only narrowly or not at all. p ≥ 0.05 is a plausible outcome, and then the verdict is "continue".
- Exact: 0–2 per arm.

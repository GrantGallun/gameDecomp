# Loop-shape normalisation of m2c drafts: pre-registration

Written 2026-09-30, after the draft census (`../draft-census-20260930/RESULTS.md`) and after building
`solver/c_stmt.py`, `solver/loop_shape.py` and `solver/skeleton.py`. Nothing below was measured when this was written.
The mechanism was inspected on 4 small drafts only (strchr, suspendGameTask, __MusIntFindChannel,
hasPendingRaceReplayCourseGridEntry), and only as source, never compiled.

## Frame

L1: every function that satisfies all of these:
- not solved (the census definition);
- not in the sealed held-out 50;
- its assembly-only m2c draft contains a goto, or a `do`/`while` loop (census `draft` features).

The starting points are the clean binary-type drafts (`solver.binary_type_draft.variants`, source-independent, the
route behind the 277 clean exacts). No reference source is involved. Attempts go to a trial database only.

## Arms, per function

- **raw**: every binary-type draft variant, compiled as is.
- **loops**: raw plus `loop_shape.variants` of each raw variant (canonical, unrotated, for, for-all, assign-in-test).
  The union includes raw, so loops can never be worse on a best-of basis. The tests below are therefore about
  how often and how much it is better.

Metrics: best `site_edits.gradient` (compiled, instruction distance, register distance) and best
`skeleton.distance` among each arm's compiled attempts.

## Predictions

- **P1 firing:** `loop_shape.variants` yields at least one variant for at least 70% of drafts containing a goto.
- **P2 skeleton:** among functions where both arms compile, loops has a strictly lower best skeleton distance in at
  least 30%.
- **P3 gradient:** loops has a strictly better best gradient in at least 25% of functions where both arms compile.
- **P4 exact:** at least 1 exact from a loop variant that the raw drafts did not reach.

If P2 or P3 holds, the variants go into the site-edit search as a draft-time lane, measured on the development frame
against U2, and then the sealed 50 under the usual sign-test rule.

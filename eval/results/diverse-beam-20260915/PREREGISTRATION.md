# Family-diverse register beam: preregistered A/B

Written 2026-09-15, before any arm ran.

## Question

Does filling the register search beam with the best candidate from each mutation family, before a
second candidate from any family (`regalloc_search.search(..., diverse=True)`), recover the one
function the new generators lost without giving up what they gained?

Motivating residual: in `eval/results/uopt-guided-20260915` arm A (old generators) was exact on
`updateEndingCreditsSlashRisingStar` and arm B (all generators) was not, even at budget 300. The beam
logs show pruning, not budget. At depth 1 the new `typed_reread:temp_t1` (0,9,11) and two tied
`local_type` variants (0,10,13) took the three slots, and `stmt_move:3->5` (0,10,13) was dropped.
A reached exact from that parent at depth 2.

## Setup

- **Cohort, search settings, compile callback:** identical to `uopt-guided-20260915`
  (`regalloc_plus_le2`, 145 functions; beam 3, depth 4, budget 150; `workspace.score` in an isolated
  `regalloc_probe.Bench`).
- **New arms**, same starting sources:
  - **D**: all generators, diverse beam.
  - **E**: old generators (no `typed_reread`, `truth_test`), diverse beam.
- **Reused arms:** A and B come from `uopt-guided-20260915/run-1/rows.jsonl` and are not re-run. The
  search is deterministic given the oracle. As a replication check, B is re-run on 10 functions in
  sorted-name order, first 10 of the cohort. If any result (exact, compiles) differs, the reuse is
  invalid and A/B are re-run in full before anything is scored.

## Predictions (primary)

1. **D is exact on `updateEndingCreditsSlashRisingStar`** (fire test at search level).
2. **D ≥ B** in exact functions (B = 80).
3. **D loses at most 1 of B's exact functions** (`B_not_D` ≤ 1).

Secondary, reported but not gating:

- E − A isolates the beam change on the old generator set.
- Median candidate compiles on functions exact in both B and D.

## Decision rule

If predictions 1–3 all hold, stage the generators plus `diverse=True` as one amendment. That amendment
must be re-staged over the live frozen code, which now includes compile-chain. If any prediction fails,
neither ships and the failure is recorded here. No arm settings are changed after the first run.

A prediction that fails is reported as failed.

## Run notes

- 2026-09-15 11:0x: run-1 paused at 29 rows to free CPU for the progress census.
- 11:17: `const_store_local` was added to the main-tree generators. `ab.py` now disables it in every arm, so the
  remaining rows use the same generator set as the first 29 (all written by 10:59) and the reused A/B rows.

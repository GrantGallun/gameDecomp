# Replication: does continued exploration beat early stopping? — preregistration

Written 2026-09-28 before any replication run, after the first trial (`PREREGISTRATION.md`) finished.

## What the first trial showed

18 functions (1–4 residual faults), budget 128. `production` matched 0; `mutation_count`, `evolvability`
and their diverse variants each matched the same one function (`updateEndingCreditsCharacterAura`, both
seeds). `production` spent a median of 25.9 of its 128 compiles: its beam runs out of improving and
sideways moves and the search ends with budget unused. The new arms spend the full budget; the match came
at 30–84 compiles. The branching-count control matched too (M2 failed), so the working hypothesis is
**continuing to expand when the gradient stalls**, not evolvability's sampling specifically. One function
is an anecdote; this run tests it.

## Design

60 fresh functions: the next 60 of the same pool by residual size (4–11 faults), no overlap with the
first 18 (`cohort.py 60 3 18 -rep ...`, seed 20260928). Arms `production`, `mutation_count`,
`evolvability`; seed 0; budget 128; beam 3, depth 4, preview 64, probes 2, explore 0.2. Three shards.
Same harness, certificate-plus-frontend exactness, all compiles charged.

## Predictions

- **R1** `mutation_count` and `evolvability` each match more distinct functions than `production`.
- **R2** Every function `production` matches, both other arms also match.
- **R3** Among functions that `production` does not match, `production` stops with at least half its
  budget unused in the majority.
- **R4** `evolvability`'s best gradient is better than `production`'s in more functions than it is worse.

If R1 fails at this size, the first trial's match was luck, and early stopping is not the lever it looked
like. If R1 holds, the campaign candidate is the simplest form: when the gradient beam empties, keep
expanding with the remaining budget.

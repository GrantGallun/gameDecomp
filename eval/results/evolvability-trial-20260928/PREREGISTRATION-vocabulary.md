# Selection after vocabulary expansion — preregistration

Written 2026-09-28 before any run. Motivation: Astra's review (`docs/evolvability-diagnosis-review-20260928.md`)
and `trajectory_direction.out`. The earlier trials compared selection policies with a vocabulary that could not
reach most answers, and my diagnosis graded only the returned best source. Graded over every explored source
(evaluation only), every arm reached a source structurally closer to the reference than its root in most
functions (production 60/77, evolvability 66/77), yet returned one in only 6–10: progress was explored and not
kept. The open question is whether selection matters once the vocabulary includes `scalar_coalesce` and
`scoped_field` (both live in the campaign since checkpoint 33521).

## Design

Fresh cohort: 60 pending functions from the current campaign state (pool 477), unmatched in both ledgers,
passing the register-search gate, none used by the two earlier cohorts (excluded by name), smallest residuals
first (12–20 faults: the easier ones were used earlier). Arms: `production_coalesce` (the live campaign's
configuration) and `evolvability_coalesce`; `scoped_field` on in both. Seed 0, budget 128, beam 3, depth 4,
preview 64, probes 2, explore 0.2. Two shards, run alongside the live campaign; budgets count compiles, not time.

## Predictions

- **V1** `evolvability_coalesce` matches at least as many distinct functions as `production_coalesce`, and
  loses none that `production_coalesce` matches.
- **V2** (retention, evaluation only) the returned best source is structurally closer to the reference than the
  root in more functions under `evolvability_coalesce` than under `production_coalesce`.
- **V3** `evolvability_coalesce`'s best gradient is better than `production_coalesce`'s in at least as many
  functions as it is worse.

A 0–0 match outcome is likely at this residual size; V2 and V3 are then the informative results. The reference
grades only (memory key-distance-dev-only); exact matches require the certificate and the frontend check.

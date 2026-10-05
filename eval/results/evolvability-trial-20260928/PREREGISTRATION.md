# Mutation-selection trial (evolvability) — preregistration

Written 2026-09-28 before any arm ran. Mechanism: `solver/regalloc_search.search(selection=...)` (Astra,
`eval/results/research-suite-20260928/MUTATION-SELECTION.md`); harness `eval/research_suite` (`freeze`, `run`).

## Cohort

18 pending functions from the live campaign, unmatched in both ledgers, passing the campaign's
register-search gate, with the smallest residuals (1–4 faults; pool 553; `cohort.py`, seed 20260928).
Three disjoint shards, one frozen bundle each. Sources are current campaign sources; assistance is
`header_assisted` or `unknown`, never claimed unassisted. Results are development evidence, not
capability numbers.

## Arms and budget

`production` (the campaign's configuration: gradient selection, enabling roots, optimizer-key reuse with
certificate rechecks and audit), `production_diverse`, `mutation_count`, `mutation_count_diverse`,
`evolvability`, `evolvability_diverse`. Seeds 0 and 1. Budget 128 real compiles per run, shared by probes,
rechecks, audits and restarts; beam 3, depth 4, preview 64, probes 2, explore rate 0.2.

## Primary outcome

Distinct functions matched per arm (certificate plus frontend; union over seeds), and paired per-function
comparison against `production` (matched in arm only / in production only).

## Predictions

- **M1** `evolvability` matches at least as many functions as `production` and loses none that
  `production` matches in the same seed.
- **M2** `mutation_count` (raw branching count, a control) does not beat `production`; counting successor
  sources is not expected to carry information.
- **M3** On functions neither arm matches, `evolvability`'s best gradient is at least as good as
  `production`'s in more functions than it is worse.
- **M4** Probe and recheck compiles take no more than half of `evolvability`'s budget on average.

A null on M1 is informative: with 1–4-fault residuals, production already reaches many matches, so a
selection policy must add matches or reach them in fewer compiles to earn a campaign trial. Secondary:
compiles to first exact per function, for functions matched in both arms.

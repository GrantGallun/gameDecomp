# Trace-guided register search: preregistered A/B

Written 2026-09-15, before any arm ran.

## Question

Do (1) the two generators that came out of the uopt trace diagnosis (`typed_reread`, `truth_test`)
and (2) trace guidance (diagnose the baseline and each frontier member, try the families preferred
for the first wrong decision first) improve the campaign's register search?

## Setup

- **Cohort:** `regalloc_plus_le2` from `eval/results/regalloc-20260913/cohort.json` (145 functions,
  register allocation is the largest fault class and at most two other faults), starting from the
  cohort's recorded campaign sources. This is the set the 20260913 generators were *not* developed on.
- **Search:** `solver.regalloc_search.search`, the campaign's code path, beam 3, depth 4, **budget 150**
  (lower than the campaign's 300 to bound runtime; recorded as a limitation).
- **Compile callback:** mirrors the live `agentrepair._regalloc_search` hook: `workspace.score` into a
  scratch attempt log inside an isolated `eval.regalloc_probe.Bench` workspace. Exactness is that oracle.
- **Arms**, per function, same starting source:
  - **A**: generators without `typed_reread` and `truth_test` (the generator set before 2026-09-14).
  - **B**: all generators, no trace.
  - **C**: all generators + trace guidance (`uopt_diagnosis.traced_compile` + `diagnose`, trace budget
    1 + beam × depth).
- Caveat stated in advance: 6 of the functions (roster icons, five SlideIns) are the motivating
  residuals of the new generators, so B's gain over A on those is not independent evidence. They are
  reported separately.

## Predictions (primary)

1. **B ≥ A** in exact functions, and B finds the 6 motivating functions (fire test at search level).
   Excluding those 6, B − A is reported as the generators' transfer.
2. **C ≥ B** in exact functions.
3. On functions exact in both B and C, **C uses fewer candidate compiles** (median), not counting
   trace calls. Trace calls are reported separately, since each costs three compiles.

A prediction that fails is reported as failed. No arm settings are changed after the first run.

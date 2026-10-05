# Dominant-register sample: preregistration (2026-09-13)

**Sample.** `dominant-sample.json`, taken at checkpoint 16156. The pool is 565 pending functions where
register_allocation is the largest fault class and more than 2 non-register faults remain, excluding every function
in `cohort.json`. 50 were drawn with seed 20260913. No function in the sample was looked at before this run.

**Generator set.** Frozen as of this commit of `solver/regalloc_mutations.py`: all families, including those added
while closing the 78.

**Procedure.**

    python -m eval.regalloc_probe search --cohort dominant-sample.json --cohort-name dominant_sample \
        --out dominant-1 --workers 2 --budget 300 --beam 3 --depth 4

**Measures, reported per function and summed.**
1. Object-exact count.
2. Non-register differences: baseline against best, the first gradient component.
3. Register-differing instructions: baseline against best.

**Reading, decided in advance.**
- If at least 5 of 50 go exact, or the median non-register difference falls by at least 25%, the artefact generators
  also reach structural faults. Running all 565 is then worth it.
- If fewer than 5 go exact and non-register differences do not fall by 25%, the generators only move register faults
  here. The structural classes need their own tools, and that goes in the hypothesis graveyard.

## Result (2026-09-14, all 50 complete)

| measure | baseline | best |
|---|---|---|
| object-exact | 0 | **2** (initSpiralCourseObject, updateFallingMenuSnowflakeSway) |
| median non-register differences | 27.5 | **18.5 (−33%)** |
| summed non-register differences | 2017 | 1677 (−17%) |
| median register-differing instructions | 30.5 | 27.0 |

Exact functions count at their baseline gradient in the "best" column, so the falls are understated.

Outcomes: 35 improved, 13 no gradient progress, 2 exact.

**Reading, as decided in advance.** Fewer than 5 went exact, but the median non-register difference fell by more
than 25%. So the generators also reach structural faults in this population.

Running all 565 is already happening: the campaign gates `regalloc_search` on register-dominant nodes, which is this
pool (453 were queued at checkpoint 16986).

**Caveats.**
- The run was interrupted by a WSL restart and resumed.
- Two functions (guPerspectiveF, initMainMenuSceneModelParts) spun for about 4.5 hours in a backtracking regex in
  `load_modify_stores`. They were rerun after the linear-gap fix (`eval/results/regalloc-hotfix-20260914/`).
- That fix gives identical generator output wherever the old form terminates: checked on 437 campaign sources,
  `hang-exposure.json`.

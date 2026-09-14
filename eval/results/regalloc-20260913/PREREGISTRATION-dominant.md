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

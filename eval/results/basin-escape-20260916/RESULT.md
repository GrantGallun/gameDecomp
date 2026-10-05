# Basin escape: a score drop that changes the fault class escapes; a bigger drop that doesn't, doesn't

Measured 2026-09-16 against `kb-sbk1.sqlite` (31,123 attempts). Pre-registered before running —
the hypothesis, the threshold and the null were fixed in `basin_escape.py`'s docstring, and the
confound control lives in a separate file so the pre-registered run was never edited after results.

## Result

**A setback that changes the residual's fault class escapes ~10x more often than one that doesn't.**

| group (explored setbacks) | n | escaped | rate |
|---|---|---|---|
| changed fault class | 16 | 6 | **0.3750** |
| same fault class | 223 | 8 | **0.0359** |

Strict escape (`descendant > parent`): diff **+0.339**, z=5.58, p<0.0001.
Material escape (`descendant > parent + 1.0`): diff **+0.152**, z=2.80, p=0.005.
Pre-registered rule was "≥5 points and p<0.05" → **SIGNAL**, on both thresholds.

## The confound, and why it does not explain this

Changed-class setbacks are also *bigger* moves — median score drop 10.16 vs 0.87 — so "changed
class" was collinear with "big drop", and "explore big drops" is a much weaker claim. Stratifying
by drop size separates them:

| drop band | changed | same |
|---|---|---|
| [0, 1) | 0/1 | 4/101 (0.040) |
| [1, 3) | 1/2 | 4/83 (0.048) |
| [3, 10) | 2/5 (0.400) | 0/27 (0.000) |
| [10, 100) | 3/8 (0.375) | 0/12 (0.000) |

Decisive cells:

- **same-class, drop ≥ 10: 0/12 = 0.000.** Magnitude alone never escaped here, at any size.
- **changed-class, drop ≥ 10: 3/8 = 0.375.**
- **changed-class, drop < 10: 3/8 = 0.375** — *identical* rate. The class change is doing the work,
  not the size of the move.

So the scalar rule ("explore when the score drops a lot") is **not** supported: the same-class
column stays flat at zero while drops grow. The fault profile is carrying information the score
does not.

## What changes class, in practice

The setback population is overwhelmingly stuck in one basin:

```
regalloc -> regalloc      3583        (91.8% of all setbacks)
structural -> structural   183
regalloc -> structural     101
structural -> regalloc      18
```

Only **127 of 3,904 setbacks (3.25%)** changed class at all. So the finding is not "explore
setbacks" — it is "explore the 3% of setbacks that cross between the `regalloc` and `structural`
basins." The pipeline's own search is parked in the regalloc basin and the escapes come from
crossing out of it.

## Replication of the previous number

| | published | this run |
|---|---|---|
| edges improving on parent | 6.0% | **5.99%** (348/5,807) |
| explored setbacks later beating parent | 6.7% | **5.86%** (14/239) |

The first replicates exactly. The second is close but not identical; this run additionally required
both ends compiled, a present diff, the parent in the compiled set, and parent score < 100 (no
headroom). The 0.8-point difference is the eligibility filter, not a contradiction.

## Caveats — all of them

1. **N is small.** 16 explored changed-class setbacks; 8 and 8 in the bands; 12 in the decisive
   same-class cell. The separation is large enough to survive it (Wilson 95% CI for 6/16 is
   ≈[0.18, 0.61] vs ≈[0.02, 0.07] for 8/223 — non-overlapping), but 0/12 does not bound the
   same-class big-drop rate below ~25%. Treat the direction as established and the magnitude as not.
2. **The sample is selected.** Changed-class edges are explored at **2.1x** the rate of same-class
   ones (12.6% vs 5.9%, z=3.10, p=0.002). The pipeline already prefers them, so these are the ones
   it judged promising. Nothing in this data can correct for that; **only randomised exploration
   gives an unbiased estimate**, and that is the cheap next experiment.
3. **Escape is only observable when explored**, so every rate here is conditional on a choice.
4. **Score semantics:** higher is better, 100 = exact, 0 = did-not-compile (verified: `exact=1` →
   score 100.0 in all 411 rows; `compiled=0` → 0.0 in all 7,331). The `attempts.score` comment in
   the schema says "lower is better, 0 = match" and is **wrong** — worth fixing before it causes a
   real bug.
5. `functions.best_score` is NULL for all 2,113 `matched` functions, so best-score queries must go
   through `attempts`, not `functions`. `eval/trajectory_factory.py` already does this correctly.

## What to do with it

1. **Replace the option-value proxy.** `research_loop.py` uses `O = headroom × repairable_share`,
   which rewards residuals a deterministic pass already owns. The measurement says the informative
   quantity is *whether the candidate crossed a basin boundary* — observable from the fault profile
   at zero cost, before any descendant is explored.
2. **Do not chase this by exploring setbacks at random.** Only 3.25% of setbacks change class, so
   random exploration spends ~30 attempts to find one. The lever is *manufacturing* class-crossing
   moves deliberately — which is a compiler-quirk question (which C transformations move a residual
   from regalloc faults to structural faults), and that is the compiler-source work.
3. **Randomise exploration to unblock the estimate.** A short run where setback exploration is
   chosen by coin flip rather than by the controller removes caveat 2 and makes the effect size
   real rather than selected. Cheap, and it is the same experiment the controller needs anyway.

## Reproduce

```bash
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 .cache/basin/basin_escape.py"
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 .cache/basin/basin_control.py"
```

Fault classes come from `solver/signals.py:analyse` applied to each attempt's `diff_summary` — the
same classifier the repair pipeline uses, so this measures the pipeline's own notion of a residual.

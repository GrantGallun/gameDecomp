# Mutation-selection trial and replication — results

Preregistrations: `PREREGISTRATION.md` (first trial), `PREREGISTRATION-replication.md`. Scorer output:
`replication-score.out`. Raw: `/home/grant/decomp/experiments/evolvability-{trial,replication}-20260928/`.

## First trial: 18 functions, 1–4 residual faults, 6 arms, 2 seeds, budget 128

| arm | functions matched | best gradient vs production (b/w/t, function-seed pairs) |
|---|---:|---|
| production, production_diverse | 0 | — |
| mutation_count | 1 | 2 / 0 / 34 |
| mutation_count_diverse | 1 | 4 / 0 / 32 |
| evolvability, evolvability_diverse | 1 | 6 / 0 / 30 |

All four new arms matched `updateEndingCreditsCharacterAura` (both seeds; header-assisted; new to both
ledgers). M1 held, M2 failed (the branching-count control also matched), M3 held, M4 unmeasured (probe
flags are not in the per-event records read). `production` spent a median 25.9 of 128 compiles.

## Replication: 60 fresh functions, 4–11 residual faults, 3 arms, seed 0, budget 128

| arm | functions matched | budget spent (median) |
|---|---:|---:|
| production | 0 | 76.0 |
| mutation_count | 0 | 128.3 |
| evolvability | 1 (`updateEndingCreditsIdleSparkle`, header-assisted, new) | 128.3 |

- **R1** partly: evolvability 1 > 0; mutation_count 0, not greater. The "keep going" control did not replicate.
- **R2** held: production matched nothing, so nothing was lost.
- **R3 failed**: 27 of 60 production runs stopped with half the budget unused, not a majority; on harder
  functions production uses more of its budget (median 76 vs 25.9).
- **R4 failed**: evolvability's best gradient was better than production's in 3, worse in 6, tied in 51.

## Reading

Across both trials (78 functions) the exploring arms matched 2 functions that production did not, and
production matched none, one in each trial and each a different function. That is suggestive, not a
policy result: 2 versus 0 is small, the branching-count control failed to replicate, and on the harder
cohort evolvability traded gradient progress for exploration (worse more often than better). The early-
stopping premise holds on the easiest functions and weakens as residuals grow.

## Corrections (2026-09-28, after Astra's review, `docs/evolvability-diagnosis-review-20260928.md`)

The diagnosis below overstated three things; read it with these corrections.

- **"The selection policy is not the gap" is withdrawn.** `diagnose.py` graded only each arm's returned
  `best_source`, which a plateau leaves at the root even after exploring. Graded over every explored source
  (`trajectory_direction.py`, `trajectory_direction.out`, evaluation only), some explored source was
  structurally closer to the reference than the root in 60/77 functions for production, 66/77 for evolvability
  and 67/77 for mutation_count, but the returned best was closer in only 6, 7 and 10. The searches move toward
  answers and do not keep those steps, because the gradient does not reward them. Vocabulary gaps are real
  (coalescing and scoped_field matched functions no policy reached), and so is retention.
- **The routing recommendation is withdrawn.** Excluding non-register residuals from register search would have
  blocked both live `scoped_field` matches of checkpoint 33521 (`updateRacePlayerMode37AerialTrick` and
  `Mode51AerialTrick`, starting residual `[1,2,2]`). `gradient[0] > 0` is not an impossibility result.
- **The coalescing probe's accounting**: 39 candidate compiles plus 11 baselines = 50 scored compiles, and
  `att.exact` is the object certificate without the frontend gate. Both winners were later verified with the
  certificate and a passing frontend in `eval/results/coalescing-factorial-20260928`.
- The diverse variants ran only on the first 18 functions; the 78-function tables do not cover them.

Next experiment: selection after vocabulary expansion, `PREREGISTRATION-vocabulary.md`.

## Selection after vocabulary expansion (60 fresh functions, 12–20 faults; `vocabulary-score.out`)

| arm | matched | returned best closer than root | explored a closer source | budget spent (median) |
|---|---:|---:|---:|---:|
| production_coalesce (live campaign config) | 0 | 7 | 51 | 125.3 |
| evolvability_coalesce | 1 (`updateEndingCreditsTransitionLogoWipeOpen`, 44 compiles, header-assisted, new) | 9 | 54 | 128.4 |

V1 held (1 vs 0, none lost). V2 held weakly (9 vs 7). V3 failed: evolvability's best gradient was better in 6,
worse in 14, tied in 40.

**Across the three trials (138 distinct functions)** evolvability matched 3 functions production did not, one
per trial and all different; production matched none evolvability missed. A one-sided sign test on 3 vs 0 gives
p = 0.125: consistent, not yet conclusive. Evolvability also makes the gradient worse more often than better on
harder functions, so it trades gradient progress for occasional matches. Both policies keep very few of the
closer sources they explore (9 and 7 of 54 and 51): retention is the larger, still unsolved gap, and needs a
pipeline-computable signal that recognizes those steps (the reference can only grade).

## Diagnosis: what is going wrong (`diagnose.py`, `diagnose.out`; reference used only to grade)

1. **Two thirds of the cohort is not a register problem.** 52 of 78 roots have non-register residual
   differences (gradient component 0 > 0) although they pass `register_dominant`. No arm matched any of them.
2. **The selection policy is not the gap; the mutation vocabulary is.** Against the reference, every arm's
   best candidate was at the same structural distance as its root in 24 of 26 register-only functions and
   37-38 of 52 structural ones; where they moved, toward and away were roughly balanced. The edits the answers
   needed are C-shape edits no family generates: coalescing m2c's per-value temporaries into one variable
   (`stepRaceMotionLooping*`), a callee taking more arguments than the prototype (`FrandVolume`), a re-rolled
   loop (`alCopy`, which IDO unrolled by 4), an argument alias local plus a stack object of the right shape
   (`func_800647E0`, `func_80064D88`), typed struct pointers with a constant held in a variable
   (`loadRaceMotionJointAnimationFrame`).
3. **One missing family, measured without the reference** (`coalesce_probe.py`, `coalesce_probe.out`): merge
   two scalar locals when every use of the second follows the last use of the first. It fired on 11 of 78 roots
   and matched 2 in 39 compiles (`stepRaceMotionLoopingAnimation`, `stepRaceMotionLoopingJointAnimation`, both
   header-assisted, new to both ledgers), where every search arm had spent 54-107 compiles on each without
   proposing it. `solver/local_web_merge` covers only pointer locals in opposite if/else arms.

A replacement policy is not justified. The narrower candidate, not yet tested, cannot lose a match
production finds: run production unchanged and spend only the budget it leaves unused on evolvability
from its best state.

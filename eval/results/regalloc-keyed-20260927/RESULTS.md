# Keyed resolution and site ranking in register search — results

40 functions, budget 200 compile-equivalents per arm, three arms each from its own baseline.
Raw: `/home/grant/decomp/experiments/regalloc-keyed-20260927/results.jsonl`; scored by `analyze.py`
(`analysis.out`); K3 follow-up by `phase_check.py` (`phase_check.out`).

| prediction | result |
|---|---|
| K1 ≥25% of evaluations resolved by key | **held**: 8,180 / 13,396 = 61.1% |
| K2 zero key violations | **held**: 0 |
| K3 keyed path = plain path (prefix) | **failed as written**: 7 of 40 functions diverged, all at evaluation 100 |
| K4 keyed ≥ plain matches, none lost | **held trivially**: 0 vs 0 — neither arm matched any function |
| K5 key/compile time ≤ 0.2 | **held**: 0.040 s vs 0.327 s = 0.123 |
| R1 ranked ≥ keyed matches | held: 1 vs 0 (`updateEndingJamHopRightToIdle`); one function is not evidence |

## K3: what failed, measured

`search` gives phase 1 half the budget when enabling roots exist, then spends the rest on the roots. The
prediction assumed one budget. All 7 divergences are at evaluation 100 = plain's phase-1 cap, where plain
takes an enabling root and keyed, whose evaluations cost less, is still in phase 1. Rerun with depths
recorded (`phase_check.out`):

- in all 7, plain's phase-1 labels are an exact prefix of keyed's (100 vs 101–171 evaluations);
- the rerun reproduces the original plain path exactly in all 7 (the search is deterministic);
- phase 2 differs in 3 of 7, where keyed's longer phase 1 ended at a different best source.

So no decision changed because of a key; the divergence is budget allocation across the phase split. The
prediction was wrong, not the equivalence. Per the preregistration this still blocks enabling it without
a decision from the owner.

## Cost and progress

| arm | spent | candidates evaluated | wall | best gradient vs plain |
|---|---:|---:|---:|---|
| plain | 7,762 | 7,722 | 2,621 s | — |
| keyed | 7,230 | 13,396 | 2,588 s | 11 better, 29 tie, 0 worse |
| keyed+ranked | 7,126 | 12,609 | 2,519 s | 12 better, 27 tie, 1 worse |

Keyed evaluates 73% more candidates per unit of budget and reaches a better register gradient in 11 of
40 functions without being worse in any. At budget 200 that did not convert into a match.

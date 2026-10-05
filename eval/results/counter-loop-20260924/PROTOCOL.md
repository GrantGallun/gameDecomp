# Paired population trial: counter_loop (H6, strength_inverse.counter_loop_variants)

Written 2026-09-24, before any compile in this directory. Rule: eval/results/branch-layout-20260924/PROTOCOL.md A2
(H6 confirmed 3/3). Fire tests (`fire_counter.py`): all five 99.936 siblings reach 100.0 with the family's first
proposal (parallel-counter merge + derived-variable loop + dropping the counter's `i++; i--;` no-op pair).

Code: code-v13 = code-v12 + overlays `solver/strength_inverse.py` (counter_loop_variants) and
`solver/regalloc_mutations.py` (family `counter_loop` after `index_form`). Nothing else changes.
Runs (budget 32, same scheduler): 1. the 224 frozen sources, paired against code-v12's run 1
(branch-shape-v11-20260924 rows); 2. round 4 from the same round-3 starts, paired against code-v12's round-4 treatment.
Acceptance, fixed now: keep the family wired only if run 1 loses 0 exacts against code-v12. New exacts: the run's
independent recompile, then record.py (ratchet), then eval.status.
Prediction (not a criterion): run 2 gains the five siblings.

## Result (`analysis.json`)
Run 1: 23 exact against 18 for code-v12, **0 losses**, gains = the five siblings; no other function changed (0
improved, 0 worse). Run 2: 8 against 3, the same five gained, 0 losses. counter_loop: 5 edges, 5 exact. The WSL VM
restarted during run 1 and the launcher resumed from its atomic rows (as in the code-v10 trial).
Recorded (ratchet-checked): receipts 95890-95894, **381 -> 386**, all five project-header-assisted.
**Decision:** acceptance met; `counter_loop` stays wired.

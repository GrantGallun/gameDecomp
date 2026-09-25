# Protocol: the existing deterministic search on binary-typed drafts that compile but do not match

Written 2026-09-24 before `search.py` ran. Input: every function whose latest capability-run draft (v2 overriding v1)
compiled but was not exact. Stream: `solver.regalloc_mutations.variants` unchanged (every family the pipeline
already has, including branch_shape and o1_register_saved), evidence = the function's compiler recipe only.
Greedy: 8 children per step, the best improving child becomes the parent, 32 compiles, then one restart from the best
node with 16 more (restart-20260923). Outcomes: exact / improved / flat / baseline-not-compiled. Exact sources go
through the same confirmation, contamination screen and recording as `PROTOCOL.md`, with strategy
`binary-types-search-20260924:source-independent`. Reported: exact count, the families on exact paths, improved
count. This is production yield; no threshold.

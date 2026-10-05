# Protocol: restarting the search from its best node vs one longer search, equal budget

Written before any run in this directory.

Why: the search is depth-limited (4) and budget-limited (32 compiles); continuing 32 more compiles from the best node
closed updateRacePlayerMode40Stun (edit-effect-atlas-20260923). A 96-compile single search with older code found
nothing (measured-potential-20260922). This isolates restarting from extra budget.

All 224 population functions, code-v9 (locality run), same scheduler.
- **long**: one search of 96 compiles from the frozen population source.
- **restart**: round 1 = the locality run (32 from the same source), round 2 = 32 from round 1's best node, round 3 =
  32 from round 2's best; functions exact after a round stop. Total <= 96.

Decision: restart is adopted as the standard population search if its exacts exceed long's, with 0 functions exact
in long but not in restart counted against it; otherwise reported as no better than budget.

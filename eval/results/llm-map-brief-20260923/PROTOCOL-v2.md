# Protocol v2: the map brief with a clean prompt and a cohort it can speak to

Written before `v2.py` ran. v1 (null) is kept as it is; this changes the four things the v1 review found.

## Changes from v1
1. Framing: the brief is presented as verified compiler evidence, not "CURRENT SEARCH STRATEGY (not a proven
   diagnosis)".
2. A short prompt written here (no 13-43k-character controller context): the function's source, the evidence, and
   the instruction to return the complete corrected source file.
3. 6 calls per arm per function (v1: 2), paired seeds.
4. Cohort chosen for the brief: unsolved functions of the locality run whose best node has at least one faulty line
   carrying a STATED fault (offset, immediate, symbol, load/store width or signedness, surplus mask/shift/cast,
   frame size), ranked by number of such lines, top 16, frozen held-out sets excluded.

## Arms (the only difference is the evidence block)
- A: the raw instruction diff (the standard evidence, unlocalized).
- B: the map brief (faulty lines with their instruction pairs, stated meanings, two cross-function example edits).
gpt-oss:20b, think low, temperature 0.35, seeds 20260923 + 100*i + k (k = call 0..5), identical across arms.
Every response's source is compiled through the population probe driver and logged.

## Decision
Brief helps if B has more exacts than A, or equal exacts and B's best-score gain beats A's on more functions with a
one-sided sign test p <= 0.10. Otherwise null. Also reported: invalid responses and compiling rate per arm.

# Protocol: does a localized repair brief from the C-to-diff map help the model repair?

Written before `run_ab.py` ran.

## Why this is not another prompt-enrichment test
CLAUDE.md lists prompt enrichment as dead (7 nulls) and found declared-type context harmful. Those added
background. This brief is localized and actionable: for each C line the compiler attributes a differing instruction
to, the line, the target-vs-candidate instructions from that line, and (where the diff states it) what the line
should produce; plus up to two recorded edits from OTHER functions that removed the same residual class on the
line they edited. No reference source, no target C.

## Design
- Functions: the 16 highest-scoring unsolved functions of the locality run whose best node has a source-bound
  attribution and at least one faulty line, excluding frozen held-out sets (agentrepair refuses them).
- Both arms: `eval.agentrepair.run`, gpt-oss:20b, same seed per function, max_calls = 2, depth 2, beam 1,
  structured output, compile and continue on residuals; root = the best node's own attempt.
- Arm A: `strategy_brief = ""`. Arm B: `strategy_brief = map brief`.
- Outcome per function: exact (object-exact, independently recompiled), and best score reached minus start.

## Decision
**Brief helps** if B has more exacts than A, or (with equal exacts) B's best-score gain beats A's on more
functions than the reverse with a one-sided sign test p <= 0.10. Otherwise recorded as a null, joining the
graveyard with its reason.

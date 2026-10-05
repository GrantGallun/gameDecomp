# Feeding the C-to-diff map to the model: null

Paired A/B, 16 highest-scoring unsolved functions, gpt-oss:20b via eval.agentrepair, 2 calls per arm, same seeds;
arm B's prompt carried the map brief (`brief.py`: faulty C lines, target vs candidate instructions per line, stated
fixes only where the diff states them, two cross-function example edits). Protocol written first.

| | A: no brief | B: map brief |
|---|---:|---:|
| exact | 0 | 0 |
| functions improved | 1 | 0 |
| invalid proposals / 32 calls | 12 | 11 |
| compiling children | 9 | 12 |

Verdict: null (`analysis.json`). Recorded as HYP-20260923-01 in memory/hypothesis-graveyard.md with its caveats
(2 calls per function; near-exact cohort whose residuals are register/branch faults the brief can describe but
not state a fix for).

## v2 (PROTOCOL-v2.md): clean prompt, stated-fault cohort, 6 calls per arm -- null
Fixes from the v1 review: brief framed as verified evidence; a short prompt written here (source + evidence + rules)
instead of the controller's 13-43k characters; 6 calls per arm; cohort = the 16 unsolved functions with the most
faulty lines whose fix the diff states. Control arm A gets the raw instruction diff (same facts, unlocalized).

| | A: raw diff | B: map brief |
|---|---:|---:|
| exact | 0 | 0 |
| functions where the arm did better | 1 | 3 (sign p = 0.31) |
| parsed responses / 96 | 59 | 82 |
| compiled candidates | 34 | 57 |
| improving candidates | 2 | 3 |

Verdict: null (`analysis-v2.json`). The brief makes the model follow instructions and produce compiling code more
often (+23 parsed, +23 compiled), but its edits improve 5% of the time, against 35-45% for the deterministic
`evidence_site` rule applying the same stated facts. For stated faults the rule is the better tool; for unstated
faults the model lacks the compiler knowledge. Graveyard HYP-20260923-01 updated.

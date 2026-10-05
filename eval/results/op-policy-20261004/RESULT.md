# Operation-value policy from the campaign's own attempts (2026-10-04)

`eval/op_policy.py` (build / train / replay). Data: `~/decomp/runs/resume-pipeline-20260908/campaign.sqlite`,
337,211 attempts, 324,796 parent->child steps with the parent's residual as state, 2,013 functions.
Label: the child is exact or an ancestor of an exact attempt (7,445 attempts). Not the score change: flat steps
are on such paths about as often as improving ones (2.9% vs 3.2%), worse steps almost never (12 of 132,715).
Model: gradient-boosted trees (scikit-learn, `~/decomp/policy-venv`), split by function hash (1 in 5 to test).

Held-out steps (68,898; base rate 1.7%): AUC 0.933, average precision 0.312.

Off-policy replay on the 139 held-out functions whose recorded tree contains an exact: the SAME recorded attempts
reallocated; an operation is available only after its parent was explored. Functions reaching an exact within N steps:

| N | recorded order | random order (mean of 5) | model |
|---|---|---|---|
| 10 | 61 | 65.6 | 61 |
| 25 | 76 | 81.8 | 89 |
| 50 | 94 | 106.4 | **119** |
| 100 | 119 | 126.6 | **135** |
| 200 | 131 | 135.6 | 138 |

The model at 50 steps covers more than the campaign's recorded order at 100; at 100 it nearly covers the whole
tree. The campaign's own order is worse than random here. Limits: replay can only reorder what was tried, so it
cannot credit operations the campaign never ran; recorded attempts reflect the campaign's choices; most of these
runs used reference-assisted drafts/headers, so this is about operation choice, not clean capability. A live
comparison at a fixed compile budget is the next test.

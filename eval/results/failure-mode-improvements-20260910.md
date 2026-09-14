# Failure-mode experiments — September 10

One isolated recovery now passes both frontend and exact-object verification:
`func_80064414`. The existing candidate already matched object sections, but passed
an integer byte-address expression to a `u16 *` parameter. Converting the complete
expression preserves byte units and code generation. The general rule requires
matching header, diagnostic, source location, o32 and target call/load/offset
evidence. Its real replay is in
[general_rule.json](frontend-exact-pilot-20260910/func_80064414/general_rule.json).
This result is not integrated or counted in the running campaign's 637 matches.

The second explicit hypothesis, for `fadeOutAllMusicSequences`, failed compilation
and remains rejected. Both outcomes are in
[frontend report](frontend-exact-pilot-20260910/report.json).

The prompt comparison used six preselected historical missing-location failures,
one model call per arm per case, matching parents/seeds/schema/budgets and alternating
arm order. The only prompt change was early edit-addressing guidance.

| Outcome | Original prompt | Early slot guidance |
|---|---:|---:|
| Application-valid patches | 0/6 | 3/6 |
| Compiling and frontend-passing children | 0/6 | 1/6 |
| Exact matches | 0/6 | 0/6 |

The one frontend-passing child did not improve its parent's 26.073 score; the
parent already compiled and passed the frontend. Therefore this demonstrates
improved patch actuation on this small failure-enriched sample, not improved
matching or compilation coverage. Half the guidance responses still repeat the
missing-location mistake. Full results:
[prompt report](patch-guidance-pilot-20260910-v1/report.json).

53 focused tests passed, including opt-in prompt wiring, strict invalid-edit
rejection, target-backed rule activation and conflicting/missing-evidence declines.
The early guidance remains opt-in. The general frontend rule is available to the
main-tree normalization path. Neither change was deployed into the frozen run.
No reference function bodies were used. Headers and saved drafts are assisted DEV
context. The live service remained running after the experiments.

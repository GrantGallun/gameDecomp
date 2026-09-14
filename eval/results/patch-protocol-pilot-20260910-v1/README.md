# Three patch protocols: fixed-parent DEV comparison

Three saved failures, rotated arm order, two calls per arm/function,
4096 output-token cap per call. Two-stage spends a call selecting locations;
the other arms can produce two code proposals. Focused retry uses reduced context.
All candidates are separate trials against the same unchanged original parent.

| Approach | Functions with applicable patch | With frontend-passing compile | With score gain and frontend pass | Exact | Calls |
|---|---:|---:|---:|---:|---:|
| slots_only | 3/3 | 0/3 | 0/3 | 0/3 | 6 |
| two_stage | 1/3 | 0/3 | 0/3 | 0/3 | 6 |
| focused_retry | 3/3 | 1/3 | 0/3 | 0/3 | 6 |

| Function | Approach | Outcomes in call order |
|---|---|---|
| gameThreadMain | slots_only | application_valid; compile/frontend failed; score 0.0 / application_valid; compile/frontend failed; score 0.0 |
| gameThreadMain | two_stage | selection_valid / application_valid; compile/frontend failed; score 0.0 |
| gameThreadMain | focused_retry | application_valid; compile/frontend failed; score 0.0 / application_valid; compile/frontend failed; score 0.0 |
| drawSinglePlayerRaceHud | slots_only | application_valid; compile/frontend failed; score 0.0 / incomplete |
| drawSinglePlayerRaceHud | two_stage | invalid; ValueError: invalid location selection / incomplete |
| drawSinglePlayerRaceHud | focused_retry | application_valid; frontend pass; score 79.944 / application_valid; compile/frontend failed; score 0.0 |
| osPfsFileState | slots_only | incomplete / application_valid; compile/frontend failed; score 0.0 |
| osPfsFileState | two_stage | incomplete / selection_valid |
| osPfsFileState | focused_retry | application_valid; compile/frontend failed; score 0.0 / application_valid; compile/frontend failed; score 0.0 |

This is a small, deliberately failure-enriched development comparison, not a
general model success rate. Selection success is not counted as an applicable
patch. Application-valid means the existing parser/application gates accepted
the edit; it is not proof of public ABI or behavioral correctness. Compiler,
frontend and exact-object results are recorded separately. No semantic gain
or source integration is claimed. The live campaign is unchanged.

37 protocol and existing model-repair tests passed. Full prompts, responses,
private attempt databases, original sources, candidate C and compiler objects
are retained beside report.json. Failed trials remain in the report.

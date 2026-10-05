# Repair evidence graph and transition planner — September 22

Implemented an opt-in evidence graph, guarded repair descriptors, conditional
transition model and two-step planner. The live comparison retains all seven
known exact matches. `osGetThreadPri` takes two compiler calls instead of three;
this is a development-case scheduling improvement, not a new match or evidence
of recursive self-improvement. Predictive transfer remains unmeasured.

## What is implemented

| Component | Responsibility |
|---|---|
| `eval/repair_graph.py` | Concrete source/target/compiler/assistance identities, original worlds, receipt-bound observed edges; contradictions rejected. Reload reconstructs all derived data from validated originals. |
| `solver/repair_rules.py` | Observable residual and compiler-context features; applicable actions from the existing register-storage, address-reuse and parameter-reuse generators. |
| `eval/repair_transitions.py` | Conditional next-state counts, normalized per distinct target and action/state row; explicit unknown mass; model validation and optional recomputation from its graph. |
| `eval/repair_planner.py` | Preview up to eight real proposals per parent, rank known routes up to two steps ahead, compile one action, then recheck the next action against actual feedback. |

The planner values observed routes to exactness. It can therefore retain an
intermediate edit that reduces the scalar score. For a row supported by one
target, half its mass remains unknown; a known two-step route through two such
rows receives heuristic mass `0.5 × 0.5 = 0.25`. This is neither a calibrated
probability nor a confidence bound. Missing transitions remain unknown.

Repeated compiles and duplicate imports do not add independent target support.
Function names, local names, candidate hashes, scalar scores and future outcomes
are absent from the predictor. Compiler recipe context, current residual class
and currently applicable action signatures are present. Concrete graph states
retain full identity; the abstract model intentionally loses detail.

The fallback is the existing depth scheduler with quantum 1.18754. Cost is a
resource ceiling, not an action-ranking feature. Lookahead is capped by both
remaining compiler calls and remaining search depth. Hypothetical continuations
never enter the observed evidence graph.

The public API is `build_graph(worlds)`, `fit(graph, development_targets=...)`,
`validate_model(model, graph=graph)`, and
`run_planner(ActionOnline(...), model, budget=32)`. The caller supplies the
existing deterministic generator and durable compiler callback, as with `Online`.
This controller is not installed as a campaign or registered-tool default.

## Frozen inputs and development boundary

`freeze.json` records 431 native code/dependency hashes, model and graph hashes,
the original development world hashes, and the eight follow-up names selected
before draft generation. The native code snapshot is
`/home/grant/decomp/experiments/repair-transition-20260922/code-v1`.

Only three archived worlds entered fitting: the successful corrected runs of
`__osDequeueThread` and `osGetThreadPri`, plus Dequeue's failed parameter-first
ordering run. These are **two exposed development targets**, not held-out
evaluation. Original partition and training-ineligible markings are preserved.
The graph contains 27 concrete states, 33 edges and six conditional rows. Every
row has support from only one distinct target.

Both arms use identical initial C, target objects, compiler identities, headers,
generators, depth limit four and 32-call ceiling. Drafts use target assembly and
headers, with no reference implementation bodies. The model and all frozen
artifacts are checked before every compile; no online fitting occurs.

## Live results

| Function | Role | Control calls | Planner calls | Exact in both |
|---|---|---:|---:|---|
| `__osDequeueThread` | Development, header-assisted | 3 | 3 | Yes |
| `osGetThreadPri` | Development, header-assisted | 3 | 2 | Yes |
| `Fvibup` | Retention, source-independent | 3 | 3 | Yes |
| `Fvibdown` | Retention, source-independent | 3 | 3 | Yes |
| `Fdistort` | Retention, header-assisted | 3 | 3 | Yes |
| `loadMusicSequenceBank` | Retention, project-header-assisted | 3 | 3 | Yes |
| `__MusIntProcessWobble` | Retention, header-assisted | 12 | 12 | Yes |
| `osCreateViManager` | Previously exposed negative case | 32 | 32 | No |
| `updateRacePlayerAirborneLaunch` | Previously exposed negative case | 32 | 32 | No |
| `updateRacePlayerMode06TerrainFall` | Previously exposed negative case | 32 | 32 | No |
| `osSetTimer` | Separate follow-up draft | 1 | 1 | No: compile refused |
| `__osInsertTimer` | Separate follow-up draft | 1 | 1 | No: compile refused |
| `__osTimerInterrupt` | Separate follow-up draft | 1 | 1 | No: compile refused |

Five preregistered follow-up names had no existing target workspace:
`osSetIntMask`, `osGetCount`, `osGetCompare`, `__osGetCause`, `osStopTimer`.
They remain recorded as unavailable and were not replaced after seeing results.
This is 13 executed cases from 18 planned, not an eight-function transfer test.

There were **zero newly solved functions and zero lost exacts**. The three
negative cases retained identical best scores (76.870, 94.869 and 91.067).
All three model-guided decisions occurred on the two development functions.
Every other compiling case used fallback with zero selected model-supported
actions. The separate follow-up drafts never reached a compilable state.
Consequently this run cannot establish either positive or negative predictive
transfer of a guided action.

## What worked and what remains missing

The graph represents the useful Dequeue dependency: qualify two local storage
declarations, then reuse the resulting address. The planner preserves that
sequence and skips a futile first edit on GetThreadPri. Synthetic regressions
also verify the sequence survives a lower-score intermediate and is abandoned
when actual child feedback no longer satisfies the next guard.

The learned catalog is still narrow. It has no applicable modeled actions on
the tested nondevelopment compiling roots, and the follow-up failures are
earlier in the pipeline. Timer drafts contain unresolved member representations
(`unk10`, `unk14`, etc.); `osSetTimer` also conflicts with its included public
signature. More elaborate regression on the same six rows would not repair
those missing prerequisites.

The actionable next experiment is to feed source-bound transitions from the
existing compile-intake/signature/member-representation repair owners into this
same interface, and preregister separate functions with the prerequisites needed
to exercise those actions. The immediate measurement should be rule coverage
and correctly predicted continuations on separate functions, followed by equal-
budget exact-match retention/gains. Keep all baseline failures in the denominator.
No new model-weight training is justified by these two development targets.

## Verification and audit

The comparison used **129 control + 128 planner = 257 compiler calls**, followed
by **seven independent confirmations**, for **264 calls total**. Every call,
including compiler refusals, has a private SQLite receipt. The audit binds all
26 worlds and 238 explicit parent edges to those receipts, verifies the exact
source/certificate bindings, recomputes the fitted model from its original
worlds, and regenerates every planner decision. There were zero model calls,
no training steps, no production TU edits and no main-KB exact-set change.
These are object-section and frontend checks, not new whole-ROM integrations.

Planner histories record compile order, which may differ from generator order.
They carry `history_kind: action-history`; ordinary `Replay` and `merge_worlds`
reject them. `replay_planner` regenerates proposals and serves a recorded result
only after the exact next source, label and parent have been independently
selected. This audits the recorded policy; it does not invent counterfactual
results for unobserved actions.

Review exposed and regression tests fixed semantic reload validation, nonexact
source/target bindings, lookahead past the depth limit, reordered-history replay,
JSON object-order dependence and duplicate receipt reuse. Preview exhaustion is
also recorded only after all pending actions have actually been observed.

The scoped search/storage/tool-boundary suite has 234 tests. Machine-readable
platform results and tested module hashes are in `tests-win32.json` and
`tests-linux.json`; the live receipt audit is `paired/audit.json`. Reproduce
read-only checks using the native WSL Python with `audit.py` and
`verify_tests.py`. `measure.py` deliberately refuses to overwrite its run.

All artifacts remain training-ineligible. Predictions, compiler evidence and
development exposure stay separate; this experiment does not promote a policy,
learn a new repair generator, or demonstrate autonomous RSI.

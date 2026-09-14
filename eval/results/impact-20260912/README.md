# Impact fixes, September 12

Follow-up: post-resume monitoring found context-limit refusals. The additional
tested prompt projection is installed as `20260912-impact-context`; final active
status and release checks are in `../impact-context-20260912/`. The first live
validation below is historical. See `PROMPT_COMPACTION.md` for the saved request
replay and actual-builder local canary. No inference-quality gain is established.

The treemap audit exposed a size gap: no function at least1KiB was object-exact
at the sampled checkpoint, though that cohort contains29.6% of tracked bytes.
The selected changes address observed execution/repair failures and add yield
measurement. They do not introduce a speculative largest-first scheduler.

## Runtime changes selected

- Inconclusive differential receipts use the environment lane. The saved-state
  replay preserves all2051 evidence keys and1258 eligible jobs, while54 functions
  /34548bytes change lane and19 queued model-polish profiles become remaining
  deterministic work. Spent budgets are not reopened. See ROUTING.md.
- Compiler-introduced word-pair helpers receive a fixed, independently ROM-bound
  execution closure. Entire instruction streams are recognized; symbol names
  merely identify candidates for validation. Candidate-only helper calls must
  not perturb later opaque-call values or explicit input overrides.
- Branch histories now accompany first and secondary differential mismatches;
  same-reason failures on different executed paths stay distinct. Verdicts and
  source/panel bindings remain intact.
- Linked-global test seeds use existing large-region support instead of the
  synthetic64KiB ceiling. Overflow/resource/scratch-collision checks remain.
- Stress panels stop spending case slots on fully shadowed duplicate inputs.
  Real popup:60→64 distinct states and14→15 target-call sequences in the same
  64-case/65-trial budget. No coverage or wall-speed improvement claimed.
- `repair_yield` records accepted exact-byte transitions and inference/worker
  cost by function size and repair profile. Similarity and finite semantic passes
  earn no exact-byte credit. Dashboard shows the size-cohort totals.

The live campaign already advanced from665 to666 exact functions before the
amendment pause; that improvement is not attributable to these fixes.

## Isolated reconstruction improvement

`eval.reconstruct` now lets the model produce just an eligible entry guard while
the controller owns region accounting. A real model guard compiled successfully
using1283 prompt tokens and501 output tokens (3.10s generation). All64 execution
cases remain unfinished, with55 target blocks in3 holes; no behavioral or exact
gain is claimed. This workflow remains opt-in and isolated, with no private
history/partial candidate import into the campaign.

See ../partial-reconstruction-20260912/guard-improvement.md for the raw proposal,
rebase checks, compiler receipts, HTTP400 grammar fix and remaining limitations.

## Release and recovery

`stage.py` builds on the previous frozen feature set; `staged-manifest.json`
identifies exact file changes. Unrelated main-tree search/investigation changes
are not copied. `deploy.py` requires paused/drained state, passing release tests,
unchanged complete input pins, inventory and model before the amendment. It
preserves previous code and checkpoint references under the run's revisions.
`deployment.json` and final live validation record whether installation completed.

Installed revision `20260912-impact` at checkpoint3488 and resumed via the
standard service with200-item batches. Main:2504 tests pass (50.36s).
Frozen release:2279 tests pass (39.31s). Eight runtime module pins changed;
inventory, model identity and all acceptance gates were preserved. See
`live-validation.json` for post-resume checkpoint, heartbeat and yield counters.
At validation: checkpoint3496, all16103 pins verified, fresh running heartbeat,
3 accepted work items with new yield counters; exact count remains666. Browser
verification showed5 work items in the yield table and no JavaScript errors.

The strongest paired result is `composeFixedTransformTranslation`: unchanged
candidate source and identical64 inputs/target traces move from64 inconclusive
cases to64 passes. This establishes recovered diagnostic execution, not recovered
exact bytes or universal behavior. See `SHIFT_HELPERS.md` for admission overhead,
the opaque-call correction and complete replay provenance.

The opaque-call ordinal issue was discovered in real replay before deployment;
the earlier passing test logs/manifest are retained with `.before-opaque-fix`
suffixes. Final tests use the corrected policy. Original experiment receipts
remain bound to their old runner and panel identities.

Rejected exploration-order hypotheses and unchanged coverage are documented in
INPUT_DEDUP.md and fault-search-probe.json. Only the stress deduplication ships.

# Decompilation ordering review — September 12

Reviewed Claude's function-layout clustering and near-finished-file preference.
Installed the tested amendment into the existing campaign at checkpoint2395,
then resumed the service with200-item batches. The model endpoint was separately
restored after it had stopped; see `../runtime-recovery-20260912/README.md`.
Latest live checks are recorded in `live-validation.json`.

## What changed

- Runnable callees precede callers within equal fairness/lane bands, using the
  existing iterative strongly connected components implementation. Cycles share
  a depth and remain eligible; there is no hard dependency gate.
- Cluster counts include currently runnable work, excluding exhausted profiles,
  parked/done functions and work outside a compile-only sweep.
- Equal-sized clusters stay together by their earliest recorded binary address.
- Range unions handle aliases/nested symbols; invalid or conflicting extents
  are declined and known-address unknown extents remain uncertainty barriers.
- Gaps are explicitly heuristic file-group hints. Current function extents come
  from ELF symbols checked against ROM words; they are not a reconstruction of
  original filenames from an unannotated ROM. Reference TU assignments only
  check the layout heuristic in tests and never supply the scheduling index.
- Fresh runs record index algorithm/inventory/index hashes. Existing runs retain
  prior ordering unless explicitly amended, as this one was.

This is a scheduling preference. Fairness and repair lanes still come first;
parallel model/CPU dispatch may overlap caller and callee work. There are no
new interface claims or changes to source evidence, proposal budgets or gates.

## Validation

`ordering-replay.json` compares both queues on the same saved checkpoint2394:

| Measurement | Previous | Amended |
|---|---:|---:|
| Eligible work items |1279|1279|
| Caller precedes runnable callee, equal visit/lane band |43|0|
| Compared edges in those bands |461|461|
| Median projection, five runs |26.8ms|30.4ms|

All eligible profiles, lanes and evidence keys are identical. A missing index
reproduces the old full ordering. The current cohort maps2051 functions to175
layout clusters. No model calls were made by this ordering replay; it does not
establish improved repair throughput or yield.

- Main suite:2223 passed49.73s (`main-tests.log`).
- Exact staged frozen release:2068 passed38.58s (`staged-tests.log`).
- Required evidence audit:2842 checkable accesses agree, zero disagreements;
  remaining coverage gaps are listed in `evidence-validation.log`.
- Edge cases include long chains,5000-node cycles, fairness, exhausted profiles,
  unknown cluster entries, JSON replay, incomplete extents, aliases and duplicates.

## Installation and recovery

`stage.py` preserves the frozen worker's prior feature set and grafts only the
reviewed ordering code. `staged-manifest.json` lists three runtime modules and
two test files. `deploy.py` checks paused/drained state, complete input pins,
inventory, model identity and passing release tests before amendment.

Backup revision: `../resume-pipeline-20260908/revisions/20260912-decomp-order/`.
It contains prior code, compact checkpoint pointer and the original immutable
state-store reference. Restore code and pointer together only while paused;
the compact pointer alone is not a standalone full-state export. New-file
removal, if rolling back, must be limited to files identified as absent by the
manifest. Current sources and candidate histories were not changed by migration.

At amendment:664 object-exact,5 function-exact pending integration,55 parked,
1327 pending;2051 total. Previous inference work had already imported
`initFixedTransform` and enabled medium effort for the source-shape retry.

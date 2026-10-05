# Compiler-effect second-edit follow-up

The frozen pool contains **256** proposals across **8** evaluation functions. 250 compiled, 220 passed the frontend, and 6 failed to compile. The ordinary scorer certified **2** exact proposals, both for `MusAsk`. The independent recompile confirmed the first exact source.

The first-step pool produced no exact match. This follow-up selected the best frontend-valid gradient improvement per function, then compiled one frozen 32-proposal full mutation stream from each selected child. It did not adapt after seeing second-step outcomes.

- `MusAsk`: baseline score 84.129, gradient `[12, 10, 13]`; first edit `stmt_move:6->7` (stmt_move, receipt 154032) reached 85.903, `[11, 4, 4]`. Second edit `local_type:u16->s32@379` (local_type, receipt 154064) reached 100.000, `[0, 0, 0]` and exact certification.
- `MusAsk`: baseline score 84.129, gradient `[12, 10, 13]`; first edit `stmt_move:6->7` (stmt_move, receipt 154032) reached 85.903, `[11, 4, 4]`. Second edit `local_type:u16->u32@379` (local_type, receipt 154065) reached 100.000, `[0, 0, 0]` and exact certification.

Confirmation receipt 154068 compiled the first winning source SHA-256 `1605ad5c14a448bfbc98b2009ac8c70af5f74d217e562735ca1c94f3e3673e2f` from the actual selected parent receipt 154032. The frontend passed and the object-section certificate was exact. This is a private, header-assisted object match; whole-ROM integration was not tested.

Raw exact-ledger classification: `recovered_known_function`. Native campaign exact metadata rows: 0; research exact metadata rows: 1. These raw ledgers do not establish frontend or provenance status.

The observed two-edit path shows that a child in the first-step improvement pool can expose an exact second proposal. The generator labels and before/after gradients describe the edits and their compiled outcomes; no compiler trace was taken for this path, so they do not establish a specific register-allocation or scheduling cause.

Frozen manifest: [manifest.json](manifest.json). Second-step report: [report.json](report.json). Audit: [audit.json](audit.json). Confirmation: [confirmation.json](confirmation.json).

Measured compiler time summed across all follow-up attempts: 77.4s; concurrent stage elapsed 29.4s. One additional ordinary confirmation compile was run.

## Existing-route reproduction

The unchanged production register search also solved the original retained
source in **42 compiler calls including its baseline, 13.67 seconds**. The private
canary used normal defaults (budget 300, beam 3, depth 4), original ancestry and
an unchanged baseline; no successful child source or receipt was supplied. It
widened the local before moving the statement and reached the same exact source
hash. All 42 attempt receipts and actual parent edges were accounted for.

At the original immutable campaign checkpoint, `scheduled_profile` already
selected `regalloc_search` with budget 300 for this source. Neither that route
nor operand repair had visited it. This case needs delivery of an existing
eligible route, not another generator or the failed predictor. No private source
was imported into the live campaign. See [route-report.json](route-report.json),
[MusAsk-routing.json](MusAsk-routing.json), and [confirmed.c](MusAsk/confirmed.c).

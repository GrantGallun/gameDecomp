# Automatic integration and runtime capture follow-up

User authorized the next steps from the residual-pattern audit: wire existing
full-ROM verification into the active fast campaign, and attempt a real emulator
state capture for an integer leaf. This folder retains deployment and validation
records; earlier private integration receipts are not imported as new evidence.

## Verified live outcome

Revision `20260912-integration` installed at checkpoint 4249; checkpoint 4250
explicitly enabled `--integrate`. Fresh automatic integration on that checkpoint
verified all five selected replacements together in an 8,388,608-byte ROM:

- calculateFixedAngleBetweenXZPoints
- osSpTaskStartGo
- rmonPrintf
- updateRaceCameraMenuPreview
- updateRacePlayerPostUpdateAttack

Target and rebuilt SHA256:
`58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c`.
Checkpoint 4255 records 670 object-exact functions plus 5 ROM-verified functions,
zero pending integration, all 16,104 pins verified, and one new completed repair
item after resume. Supervisor 1003/worker 1004 were running with a fresh heartbeat.
No canonical source installation occurred. The real dashboard API rechecked
current bindings and served the matching receipt (`dashboard-live-check.json`).

The prior in-memory development sweep used immutable checkpoint 3886 and passed
in 29.88 seconds (`dev-sweep/result-1789238210890074785.json`), without saving live
state. Integration 54 and dashboard 26 focused tests pass; the frozen deployment
suite passes 2,330 tests. The final main suite, including the final receipt guards,
dashboard refinements and capture adapter, passes **2,598 tests in 53.43 seconds**
(`final-main-tests.log`).

Dashboard process 41032 serves `http://127.0.0.1:8765/`. Browser verification shows
"Whole ROM matched" and 5 current replacements; receipts/hashes are expandable
to keep the main progress information visible. No browser console errors.

## Real runtime capture outcome

The Project64 pilot now has two naturally reached entries of
`getRelocatableHeapBlockBase` at `0x80043040`:

| Real handle | Target/self return | Deliberately wrong return | Control outcome |
| --- | --- | --- | --- |
| 0 | `0x80160480` | `0x80160481` | Self passed; wrong return rejected |
| 5 | `0x801fefb0` | `0x801fefb1` | Self passed; wrong return rejected |

Paired snapshots preserve actual registers and RAM. The adapter validates
stationarity, full register widths, ROM/code binding and existing integer-leaf
restrictions. The negative control modifies assembly's returned value; it is not
a compiled-C repair or a new exact match. Returned heap buffers are outside the
captured window, so these are not complete caller-state captures.

New `solver/project64_capture.py` and its 20 tests are in the main project; no
runtime case was silently injected into the campaign. The isolated emulator was
stopped after the two captures. Repeatable commands, official producer provenance,
failed exports and final receipts: `../runtime-capture-20260912/README.md`.

## Integration contract

The optional fast-campaign sweep runs only with drained workers under the
controller's campaign lock. It selects at most five boundary-exact pending
functions and includes the previously integrated union. It reuses preparation,
source/attempt/certificate validation, disposable full builds and whole-ROM
comparison. Reconciliation checks current bindings and archived evidence before
recording `integrated`. Canonical game sources are not installed or changed.

Unchanged unsuccessful selections are suppressed using their evidence identities.
Failures remain recorded and do not become correctness claims. Object-exact
coverage and isolated whole-ROM verification remain separate in the dashboard.

## Release records

- `staged-manifest.json`: exact changed file hashes over the prior frozen runtime.
- `main-tests.log`, `staged-tests.log`: terminal test results.
- `deployment.json`, `option-deployment.json`: code and explicit opt-in amendment.
- `validation-checkpoint.json`, `live-validation.json`: frozen post-deployment
  checkpoint pointer, pin verification, outcomes and service health.

This is header-assisted development. A full-ROM match for selected replacements
does not establish all-C completion or all-input behavioral equivalence for other
nonexact candidates. Runtime capture evidence is reported separately.

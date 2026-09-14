# Automatic muted capture in the live campaign

User authorized moving the successful emulator pilot into the current campaign's
test workflow. This change adds a bounded, explicit `--runtime-plan` stage at a
drained controller boundary. The existing isolated whole-ROM integration stage
remains enabled independently.

## Workflow

1. Read the manifest whose SHA256 was bound by an explicit run amendment.
2. Launch the verified Windows capture runner using an isolated copy of the
   pinned portable emulator assets. Playback volume is zero.
3. Capture a freshly reached entry, validate paired snapshots and ROM identity,
   and stop/wait only for the runner's own emulator process.
4. Compile the campaign's currently selected C in a native private workspace,
   log the attempt, and replay that compiled candidate against the captured state.
5. Recheck source, certificate and build bindings before checkpointing results.
   Save validated captures for that function's later repair workers. Unresolved
   candidates use the existing combined synthetic/captured semantic panel.

Captures never grant or remove an exactness verdict. Unsupported execution and
capture failures remain explicit. The current manifest has two audited entry
selectors for one integer leaf, not scenarios for every game function. Fresh
capture scheduling and bounded reuse are controlled by the recorded plan policy;
ordinary unit tests exercise the machinery without launching the emulator.

## Evidence

Installed revision `20260912-runtime-capture` with the campaign paused and drained,
then explicitly enabled the SHA256-bound capture plan and resumed the service.
The live controller completed both fresh captures against selected C attempt
36449 (`getRelocatableHeapBlockBase`). Both comparisons passed; both emulator
receipts record muted playback and successful owned-process cleanup. Checkpoint
5021 verified all 16,111 pins and preserved 671 object-exact plus 5 separately
ROM-verified functions, with 19 additional repair items completed after resume.
All final live checks passed; continuation evidence is in `live-validation.json`.

Validation: 2,651 main-suite tests passed, followed by 9 new runtime-dependency
and worker-wiring tests. The final frozen suite passed all 2,399 tests. The
initial frozen collection failure exposed missing older runtime dependencies.
The release adds those modules and
grafts only capture forwarding and panel composition into the frozen workers.
Unrelated main-tree investigation features were not bundled into this release.

Both development and final staged-code real capture smoke tests also passed
against the selected compiled C, with no live-state writes. The staged run took
20.01 seconds while the full suite ran concurrently. Automatic captures run at
a drained boundary and reuse unchanged evidence for 24 hours; changed source,
plan or code pins invalidate that reuse key.

- `capture-plans.json`: audited input plans and explicit capture cadence.
- `controller-tests.log`, `main-tests.log`, `staged-tests.log`: terminal tests.
- `staged-manifest.json`: exactly installed files and previous/final hashes.
- `deployment.json`, `option-deployment.json`: frozen-code and option amendments.
- `validation-checkpoint.json`, `live-validation.json`: immutable live snapshot,
  current-source comparisons, pins and continued campaign work.

The deployment protocol is copied from the prior reviewed release, with only
the changed-pin extension check expanded to include the new JSON asset manifest.
The exporter JS has its own fixed hash enforced by the pinned Python runner.
Historical capture, integration and failed-attempt records remain intact.

# Existing integration capability and current five-candidate validation

September 12, 2026. Read-only campaign audit followed by the explicitly authorized
isolated build validation. No model calls, canonical game changes, or live campaign
status imports. Reference C was used only as the opaque TU-splice/build destination,
never for repair inference.

## Result

All five function-boundary-exact candidates selected from immutable checkpoint
**3645** pass together in a clean isolated full-game build. The rebuilt **8,388,608
byte ROM is byte-identical** to the supplied reference. Target and rebuilt SHA256:

`58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c`

The existing preflight plus batch integration path took **25.72 seconds**. All five
individual preparation checks passed; the combined build passed on its first try,
so there was no reason to bisect or run five redundant standalone full builds.
Hashes of all five canonical destination TUs remained unchanged.

| Function | Attempt | Function bytes |
|---|---:|---:|
| calculateFixedAngleBetweenXZPoints | 231 | 44 |
| osSpTaskStartGo | 1027 | 64 |
| rmonPrintf | 24961 | 28 |
| updateRaceCameraMenuPreview | 3891 | 32 |
| updateRacePlayerPostUpdateAttack | 5350 | 96 |
| Total | | 264 |

This verifies these replacements in the existing game. It does not claim a whole
autonomously recovered C game, new object-section-exact certificates, or emulator
runtime scenario coverage. The active checkpoint statuses were intentionally left
unchanged.

Durable evidence:

- `integration-audit.json`: source hashes, certificates, build-input checks, attempt
  lineage, TU/header readiness, and historical receipt inventory.
- `pending-integration-1789235727647371261/binding.json`: checkpoint manifest hash,
  audited source hashes/attempt IDs, input audit hash, and canonical TU hashes.
  The validation script checked selected node content hashes against the immutable
  checkpoint before invoking integration.
- `pending-integration-1789235727647371261/summary.json`: selections, outcome,
  elapsed time and canonical-file preservation.
- `pending-integration-1789235727647371261/1789235728276857606-prepared/manifest.json`:
  source-bound replacement manifests and complete verification lineage.
- `pending-integration-1789235727647371261/1789235728276857606-integration.json`:
  whole-ROM receipt; sibling `.build.log` and `.rebuilt.z64` preserve actual outputs.

## Existing mechanisms, actual wiring, and gap

`eval/prepare_integration.py` already checks function-boundary or object-section
certificates, source/target/reference-ROM hashes, build-input hashes, attempt/TU
lineage, headers, and conservative single-function replacement syntax. It refuses
ambiguous/preprocessor-dependent definitions or unsupported shared declarations.

`eval/integration_gate.py` already builds only disposable game copies and compares
the complete output ROM, preserving its receipt, build log and ROM. Symlinks that
could escape isolation are refused. It does not install candidates in the working
game tree or claim an all-C recovery.

`eval/completion_campaign.py:345` already preflights candidates independently;
`:359` bisects failed batches and re-verifies the combined surviving union.
Operational errors and explicit build STOP messages halt instead of encouraging
retries. Its `--integrate` option actually runs this workflow and records statuses.

The active `eval/fast_campaign.py` counts integration statuses but has no equivalent
integration stage. Checkpoint 3645 has **668 object_exact, 1322 pending, 56 parked,
5 function_exact_pending_integration**, and an empty `integrations` list. These
five are done from the repair queue's perspective and remain pending integration
because the active orchestration does not run the already implemented stage.

Historical audit found **20** actual top-level integration receipts: **16 rom_exact,
4 build_failed**. **13** archived rebuilt ROMs still exist and match their recorded
hashes. None of those manifests contains these five exact current source hashes.
Thus integration is a demonstrated existing capability, and the new combined
receipt is fresh verification of the current candidates.

Smallest useful next implementation: an explicit bounded integration sweep that
consumes an immutable fast-campaign checkpoint and reuses these existing functions,
then exposes a source/checkpoint-bound receipt in the dashboard. If live status
reconciliation is later enabled, it must verify that source, certificate and build
bindings still match; a stale receipt must not promote a changed candidate. Keep
whole-ROM integration separate from object exactness in progress metrics. A new
integration subsystem would duplicate machinery already tested here.

## Runtime-capture track: useful but different work

`eval/runtime_capture.py` and `completion_campaign --runtime-captures` already exist.
As documented in `AUTONOMOUS_INVESTIGATION.md:88`, the adapter reads an already
stopped GDB endpoint with explicit register layout and RAM windows. It rechecks
stationary register/RAM state, binds code to the supplied ROM, restricts memory to
cached RDRAM, and adds checked captures to semantic panels. Missing memory is not
silently replaced by zeroes.

It does not launch/resume an emulator, place breakpoints, infer a debugger register
map, or discover scenarios. Documented replay supports integer leaves; calls, FPU
and 64-bit operations decline. The inspected setup has no demonstrated real N64
game-state capture receipt or live emulator/debugger endpoint. The useful next step
is one explicit emulator endpoint/stop-plan smoke capture and ROM-bound replay,
followed by measured coverage expansion. Existing full-ROM equality receipts are
not evidence that this runtime track is already operational.

## Invariants reviewed

`CLAUDE.md`, `DESIGN.md`, and the existing pipeline integration map require byte
authority, build-green/canonical isolation, mechanical evidence separate from
inference, and no duplicate machinery. This audit and validation preserve those
limits. No concolic or callee-summary implementation is proposed by this artifact;
those require their own evidence of a current bottleneck and bounded contracts.

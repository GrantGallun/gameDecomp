# Checkpoint recovery and ROM verification

The reported failure was a checkpoint commit `disk I/O error`, followed by
read-only restart failures while SQLite needed to recover its rollback journal.
The journal was recovered through SQLite under paused supervisor/controller
locks, preserving the checkpoint 6383 pointer and validating all selected object
hashes. The two saved jobs were reconciled through the original frozen controller
to checkpoint 6386. All 676 object-exact and 5 integrated functions remained.

The archived 8,388,608-byte integration ROM was rehashed, together with its
receipt, source/certificate bindings and preparation artifacts. Target and
candidate SHA256 both equal:

`58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c`

Full recovery/ROM audit evidence:
`../checkpoint-recovery-20260912/rom-audit-1789253655631236484.json`,
`../checkpoint-recovery-20260912/recovery-1789253549784066050/`, and
`../checkpoint-recovery-20260912/drain-1789253571215616232/`.

The scoped fix adds locked-controller recovery of that specific SQLite error,
explicit checkpoint connection closure, and extended SQLite error diagnostics.
Dashboard reads remain read-only. Corruption and unrelated I/O errors do not
trigger recovery retries or checkpoint rollback. A subprocess crash fixture
exercises an actual hot journal; the staged frozen runtime is tested before
deployment using `stage.py` and its generated `deploy.py`.

Validation: 45 focused main-workspace tests passed; the final frozen suite passed
all 2,471 tests in 51.07 seconds (`staged-tests.log`). Initial test failures and
the final manifest are retained separately. A second agent reviewed recovery
and transaction ordering without identifying blocking issues.

Deployed revision `20260912-checkpoint-recovery` at checkpoint 6387, changing
only three runtime pins and preserving all function statuses. Resumed batch 10.
Live validation at checkpoint 6412 confirms eight newly completed work items,
681 exact/integrated functions, matching release files, and a fresh running
service heartbeat. See `deployment.json`, `live-validation.json`, and
`dashboard-rom.json` (valid ROM-exact receipt, no binding issues).

The original I/O cause is unproven. The host had approximately 84 GiB free;
the recent Windows System events inspected did not identify a disk failure.
Connection lifetime cleanup and recoverable startup address known code issues,
but do not establish that the underlying storage fault is fixed.

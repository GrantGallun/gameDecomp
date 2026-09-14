# Reviewed candidate adoption

`adopt_candidates.py` is prepared and unit-tested; the author has not run it
against the live campaign. It is an explicit operational tool for the parent
agent after normal frozen-code deployment and pause/drain checks.

The manifest fixes three corrected source hashes: the generated `strlen`
byte-test rewrite, `osCreateMesgQueue`'s opaque sentinel view, and
`fadeOutMultiplayerCourseSelectMenu`'s unsigned existing-global view. Original
pilot parent IDs are provenance only. Each fresh attempt uses the current
campaign parent ID after verifying its function, source bytes and expected
parent hash. If a pending function has changed source since the pilot, adoption
declines so the correction can be reviewed against that current source. A
previously exact/integrated function is skipped, preserving its binding.

Run directly in WSL so Python imports the deployed `RUN/code`, and choose a new
native output directory:

```sh
python3 /mnt/c/Code/gameDecomp/eval/results/data-mechanism-20260913/adopt_candidates.py \
  --run /mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908 \
  --manifest /mnt/c/Code/gameDecomp/eval/results/data-mechanism-20260913/adopt-candidates.json \
  --out /home/grant/decomp/data-adoption-20260913-new \
  --integrate
```

The script requires an existing service pause marker, `service.json` paused
without a worker PID, no inflight jobs, the campaign lock, unchanged checkpoint
pins, and the current inventory identity. It does not pause or restart anything.
It creates a private native compiler workspace per function and appends every
fresh compile attempt to the existing attempt ledger through `workspace.score`.
It neither copies any database nor imports private pilot rows.

All candidates must freshly pass the project frontend plus either the existing
object-section or function-boundary certificate. Certificates must bind the
reviewed candidate hash. Fresh residuals and bounded semantic receipts are
retained; unsupported semantic environments stay unavailable. These semantics
use the existing panel defaults, without assuming real captured runtime data.
Failed attempts remain logged, but a failed required gate prevents all state
adoption. The checkpoint is updated only through `campaign.accept` and
`campaign_state.Store.save`, after source/pin rechecks and a ratchet preserving
every previously exact/integrated binding. KB matched count must remain unchanged.
No canonical C, evidence keys or budgets are rewritten/reset.

`--integrate` then invokes the existing `campaign_integration.sweep` over the
previously integrated union and its normal bounded pending selection. Only that
fresh combined ROM gate can mark pending candidates integrated. Its normal
alphabetical limit of five remains in force, so an unrelated earlier pending
function can be selected before a newly adopted function. Omit the flag if normal
controller resume should own that subsequent sweep. Adoption itself claims no
whole-ROM verification and retains object-exact versus boundary-pending status.

Four operational-helper tests cover exact/frontend gates, immutable verified
bindings, pause/drain requirements, and manifest identity/duplicate rejection.
These are unit checks; actual paused-run adoption remains for parent review.

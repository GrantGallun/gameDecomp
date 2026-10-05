# Checkpoint 29289 recovery and one-file solver amendment

This is an execution specification for the paused `resume-pipeline-20260908`
campaign. Nothing here has been applied to its checkpoint, databases, launch
configuration, or frozen project. `inspect_checkpoint.py` is read-only.
`stage_recovery.py` wrote only `stage-29289/` under this directory; its manifest
is the compact before-image for the three jobs. The parent operator should
review `apply_recovery.py` and its `--apply` path under both campaign locks.
Its default mode is read-only validation. `stage_solver.py` produced
`solver-stage.json` from a native WSL copy of the frozen package with only the
one solver file overlaid (68 frozen tests and 70 main regression tests passed).
No `--apply` invocation has been made.

## Observed boundary

Read-only inspection on 2026-09-26 found checkpoint commit `29289`, pointer
SHA-256 `bbbf44a4707255531e21798ec5d80f676d0acae93f7b55456ee84893d9694d85`,
3314 pins with digest `43829448756f0c4fcd677982bcd02651c7332d54e515a55dfe775fb48e700296`,
998 object-exact/integrated and 24 function-exact pending integration.
Both durable pause markers existed. The old frozen
`solver/mips_differential.py` matched its pin
`61ca9a6552b3e13fb64f72a9f19a66aee294d5ed94092c08e9966f6353659f66`.
The fixed main-tree file had SHA-256
`74ebe435b7bf0fa62aca16784e0df77e3ec366f28a9a88942a5ea0e64a7d7552`.

| Original job ID suffix | Raw receipt | Post-cutoff attempts | Model proposals |
| --- | --- | ---: | ---: |
| `initAudioSynthesizer` | absent; worker crashed in `math.trunc(infinity)` | 5 | 0 |
| `drawRaceSetupSaveChoicePrompts` | evaluated, SHA `7355eefea54f3f61464212fb159e227ab777ab18264426dfef39796af5053fad` | 5 | 0 |
| `updateRaceScoreAttackRings` | evaluated, SHA `f213300ba3f95608a5b5aebe4b288e35ce22e01bae6312a9d33e1177f49fb3a5` | 6 | 0 |

The full IDs, private delta hashes, candidate bytes and raw copies are in
`stage-29289/manifest.json`. All three dispatch profiles were non-model jobs,
and all three `job['pin_sha256']` values equal the old state pin digest. All
three dispatch source files matched their recorded hashes. Neither completed
raw had a canonical receipt or `campaign_worker_imports` row yet.

Each private database occupies about 6.2 GB. The staged post-cutoff deltas
contain the attempt, edge, run, and proposal rows used by
`campaign_workers.merge`; they preserve the crashed five attempts without an
18.6 GB whole-database copy. Verify all staged hashes against the manifest
before any live change. Do not reuse any worker slot until the drain is
checkpointed and the corresponding staged delta is verified.

## Drain under the original pin

1. Keep both `service.pause` markers. Verify the supervisor and worker are
   stopped, then take `resume-supervisor.lock` and `campaign.lock` using the
   same nonblocking `fcntl` locks as the previous amendment. Fail if either is
   held. Require the exact pointer, launch hash, job IDs, raw hashes, private
   row counts, source hashes, and old pin digest above. Use the **frozen**
   package for all campaign imports and assert its `__file__` paths. Run
   `frozen_wavefront.verify_files(state['pins'])`, check the campaign DB and
   checkpoint object DB with `PRAGMA quick_check`, and capture exact function
   membership and node snapshots before mutation. These checks must run again
   just before each merge if anything external could have changed.

2. Import the two completed raw jobs in checkpoint order: draw, then update.
   Use the existing controller operations from `eval/fast_campaign.py` lines
   395-424, with the original `job` dictionaries: `validate_job`,
   `campaign_workers.merge(main_db, job['db'], job['cutoffs'], job['id'])`,
   `campaign_workers.remap`, set `private_lineage` with raw path and both ID
   maps, atomically write the canonical receipt at `job['receipt']`,
   `campaign.accept`, `repair_yield.record`, update the same completed/improved/
   exact/worker/model/compile/semantic metrics, remove only that job from
   `fast_inflight`, then `fast_campaign.project`, `summary`, and
   `campaign_state.Store(state_path).save(state, changed=(function,))`.
   Verify the committed state and canonical receipt before advancing to the
   second job. If an import was committed to the main DB but the checkpoint
   save failed, rerun the same job ID: `campaign_workers.merge` returns its
   existing mapping without duplicating rows. A conflicting canonical receipt
   must fail closed rather than be overwritten.

3. For the crashed `initAudioSynthesizer` job, confirm the raw is **still**
   absent, the node source and evidence key still match dispatch, and the
   private DB still has exactly attempt IDs `179108` through `179112` with no
   post-cutoff model proposal. Import those rows with `campaign_workers.merge`
   under the **same original job ID**. Write a distinct crash-recovery receipt
   that records the original job/profile/pin, `OverflowError` log reference,
   staged delta SHA, old-to-new attempt map, zero proposals, and the explicit
   disposition `abandoned-before-raw; retry-after-amendment`. Attach its path
   and hash to a checkpoint metadata recovery event. Remove the job from
   `fast_inflight` and save the checkpoint. Do **not** invent a completed raw,
   call `campaign.accept`, add a node job, park the node, or increment completed
   work/model counters. The node remains eligible for a fresh visit. The main
   ledger and import mapping own the five failed attempts.

4. Require `fast_inflight == []` and legacy `inflight` absent, with all three
   original `campaign_worker_imports` IDs present and mappings containing
   5/5/6 attempts. Both completed canonical receipts must link to their raw
   receipts and mapped attempt/proposal IDs. Require unchanged old pins and
   model/inventory identities, zero model proposals added, no loss of original
   exact function membership, and the expected node changes limited to the two
   accepted raw jobs. The failed node must remain byte-identical. Verify the
   pointer round trip. Preserve checkpoint commits as immutable history; on a
   partial failure, diagnose and retry idempotently from the current pointer,
   never blindly restore the old pointer over an already imported job.

The original controller should **not** be started to perform this drain: even
with `service.pause`, its `while ... or fast_inflight` loop submits any job
whose raw is absent before it processes the two ready raws. That would rerun
the crashing job under the old solver.

## Stage and apply the solver amendment

After the drained checkpoint is reviewed, copy the entire frozen project to a
native WSL scratch directory and overlay only the fixed main-tree
`solver/mips_differential.py` into the copy. Assert old/new hashes above. Run
the copied frozen differential tests plus the new main-tree regression tests
against the copied package. The main-tree tests already passed locally:
`tests/test_mips_differential.py`: 70 passed; five adjacent differential and
semantic modules: 84 passed, 2 skipped. A staged test receipt must bind the
exact new file hash before apply.

Under both locks and both pause markers, use the pattern of
`revisions/20260926-operand-delivery/apply_amendment.py`: require the **drained**
source pointer/launch hashes, zero inflight jobs, old frozen hash and pin,
unchanged 3313 other pins, DB integrity, inventory/model identity, and the
staged test receipt. Archive the drained pointer, launch bytes and old frozen
file. Install only the staged solver file; update only its key in `state['pins']`
and `launch['code_hashes']`; append a runtime amendment record with old/new
hashes, source commit, stage receipt, unchanged pin count and limits; save and
round-trip the checkpoint. Recompute `_pins` from the frozen project and repo
plus existing nonmatching pins, require exact equality with state pins, and
verify original exact membership. On failure restore old code, pointer and
launch bytes, and record any orphan checkpoint commit. Do not unpause or start
the controller in the amendment transaction.

The two completed raw receipts were produced against the **old** 3314-pin
digest and are imported before changing it. The five crashed attempts also
retain that original job ID/pin in the recovery event. The amendment changes
one code pin, so its new digest applies only to subsequently dispatched jobs.
With the failed job removed and its node untouched, the scheduler may retry
`initAudioSynthesizer` under a new job ID and the new pin after normal resume;
the old failed attempt lineage remains in the ledger. Do not copy its prior
inflight dictionary into the amended checkpoint.

`apply_recovery.py` checks the immutable original checkpoint's node object
references on every partial restart: only the two already accepted raw jobs
may differ. This protects all 998 original object-exact/integrated functions,
all 24 function-exact pending functions, the crashed node, and every other
untouched node without repeatedly hydrating the full state. Before each merge
it compares all post-cutoff attempt, edge and run rows with the staged private
delta; after merging it compares the remapped main database rows and source
hashes before accepting a node. It performs database `quick_check` once before
mutation and targeted checkpoint/receipt round trips after each accepted job.

If the process dies after copying the new solver file but before it can run
its exception rollback, **do not resume the controller**. Keep both pause
markers; take both locks and confirm no worker or newly dispatched job exists.
Inspect `campaign.drained.before.json`, `launch.drained.before.json` and
`solver.before.py` in this directory, and verify their hashes and the drained
checkpoint manifest in the object store. If no new job or receipt was created
after that drained boundary, restore exactly those three archived byte
images under the locks, then confirm old pins and the three recovered import
mappings. A newer orphan object-store commit can remain; the pointer selects
the verified drained commit. If any newer job or receipt exists, stop for a
fresh lineage audit instead of rewinding the pointer.

## Applied outcome

The recovery completed at checkpoint 29292. All 16 archived attempts were
imported under their original worker identities, the two completed jobs were
accepted normally, and the crashed job was abandoned before raw completion
without consuming its node/profile. Original exact membership was preserved.

Two guarded applies stopped without discarding work: first after a successful
import because JSON integer keys had become strings in checkpoint metadata;
second after the full drain because the archived launch's descriptive solver
hash was already stale. Regression tests now cover both. The retry compares the
launch entry with its archived value separately from the authoritative frozen
file and state pin; it does not relax the actual code check.

The third apply succeeded at checkpoint 29293. `amendment.json` records old code
hash `61ca9a6552b3e13fb64f72a9f19a66aee294d5ed94092c08e9966f6353659f66`,
old launch entry `5ed8383bb11589ddbc23bd868595ba59dd005c1e289bde87114ac3a0a1b1f5ed`,
and installed hash `74ebe435b7bf0fa62aca16784e0df77e3ec366f28a9a88942a5ea0e64a7d7552`.
It verified 3,313 unchanged pins and retained 998 object-exact/integrated plus
24 function-exact pending nodes. Both pauses were present at amendment completion.

The separate bounded resume driver was then launched. Its fresh controller log
records ordinary work completing under the amended solver. Follow
`../../frontier/progress.json` and batch receipts for current progress; the
continuation is not claimed finished. Focused recovery tests: 7 passed; bounded
resume tests: 5 passed. Fresh scoped review found no remaining material blocker.

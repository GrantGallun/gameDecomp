# Controller optimizations implemented

The main-tree controller now copies only missing attempt-run metadata, hashes every frozen file using one descriptor open, records dispatch/import/setup timings separately, and publishes session timing anchors that keep dashboard rates current during a batch. No model budgets or verifier gates changed. Live deployment is owned by the parent agent; this implementation task did not mutate the frozen runtime or running service.

## Measurements

`controller_benchmark.py` used a read-only query to obtain 5,094 historical run rows (19,437,269 bytes of values), then created isolated schema/run-only fixtures. Three paired incremental synchronization trials added ten unattached run records and compared every run row/config afterward:

| Operation | Previous | Optimized |
| --- | ---: | ---: |
| Metadata synchronization, three trials | 1.222–1.335 s | 0.047–0.053 s |
| Full 16,095-file SHA-256 verification, two trials | 1.682–1.698 s | 1.062–1.080 s |

The optimized hash still reads and hashes all bytes on every call. `os.open` uses nonblocking mode where available to avoid hanging on a FIFO replacement; descriptor metadata rejects nonregular files. No timestamp cache was introduced. Hash timings read unchanged live inputs only. SQLite fixtures preserve the Windows-upstream/native-WSL-worker placement. These are stage measurements, not full-campaign throughput claims; production attempt/proposal deltas were intentionally excluded from the small fixture. Machine-readable results: `controller-benchmark.json`.

## Validation

Focused WSL suite: **22 passed in 7.37 seconds** (`test_controller_optimizations.py`, `test_campaign_fast.py`, `test_progress_app.py`). Coverage includes complete unattached history/config preservation across multiple 500-ID chunks, repeated sync, existing collision/replay/rollback tests, changed content with restored mtime and size, missing/directory replacements, changed symlink target, FIFO rejection without blocking, and live UI rates that do not accumulate paused/stale/restarted-controller time.

## Files to deploy

- Frozen runtime: `eval/campaign_workers.py`, `eval/frozen_wavefront.py`, `eval/fast_campaign.py` (parent may add scoped worker reuse after these timing changes).
- Main dashboard service: `eval/progress_app.py`; restart required to load Python changes.
- New tests: `tests/test_controller_optimizations.py`.

Additional metrics are additive. Existing `controller_seconds` remains dispatch time for compatibility; `import_seconds` covers result validation/import/accept, and individual `pin_verify_seconds`, `database_sync_seconds`, `database_merge_seconds`, `workspace_isolate_seconds`, `accept_seconds`, `submit_seconds`, `worker_setup_seconds`, `startup_seconds`, and `result_ready_wait_seconds` expose sub-stages. Counters overlap by design and must not all be summed. `session_seconds` now includes controller startup and updates at each checkpoint. `session_live_since` and `session_pid` allow the UI to extend that measured anchor only for the matching active controller with a fresh heartbeat. Resume starts from persisted elapsed time; downtime is excluded.

Native benchmark fixtures are retained under `/home/grant/decomp/optimization-validation-20260911/controller`; Windows fixture and receipts remain in this directory. No large campaign database backup was needed.

## Bounded process reuse and supervisor follow-up

`--tasks-per-worker` defaults to one and supports values 1–64. The proposed eight-job experiment is recorded in `runtime_options`; changing the value requires an explicit amendment. A legacy checkpoint without the key means one job per process. `_worker` restores SQLite connection authorization and closes scoped runtime wrappers in `finally`, including unexpected execution errors and raw-receipt write errors. The `compiler_recipe._resolve` LRU is cleared before and after every job because its environment/file-existence observations are not fully represented by its literal arguments. Cache artifacts remain bound by their existing full input identities.

The new lifecycle test executes four sequential jobs with different slots, DBs and pin markers in one process. It verifies current-DB write denial, absence of a stale previous-DB authorizer, isolated model counters, restored runtime functions and pin marker, empty compiler-recipe cache between jobs, recovery after an unexpected `KeyError`, and recovery after a raw receipt write failure. Existing controller tests now exercise both one/eight-job process options and reject unamended option changes. Focused expanded suite: **37 passed in 11.07 seconds**. No live eight-job throughput claim is made by these tests.

`eval/campaign_service.py` now calls `worker.wait(timeout=10)` while retaining heartbeat updates. A completed batch returns immediately; a running batch still gets the prior heartbeat interval. Restart/retry/pause decisions and failure backoff are unchanged. Existing service restart tests passed. This module also requires service restart at deployment.

# Full-game campaign speed audit

September 11, 2026. Read-only inspection of the running campaign; no production
code, checkpoint, model settings, or game source changed. The isolated audit
script and its complete observations are alongside this note.

The snapshot contains 2,051 functions: 641 object-exact, five function-exact
pending integration, 1,351 pending, and 54 parked. All functions have intake
receipts. The current stage is repair, not a pytest run. The prior full WSL
regression suite took 33.76 seconds (2,133 tests).

For 61 recent completed semantic-counterexample work items, mean reported worker
time was 53.82 seconds. Adjacent start intervals under 300 seconds contained
19.66 seconds mean / 17.20 seconds median outside reported worker time. These
intervals exclude long interruptions and are a throughput sample, not a final
completion estimate. Transport consumed 2,401.38 of 3,283.00 reported worker
seconds, about 73%. Transport elapsed includes any server queueing and is not
pure GPU execution time. The preceding 60-item inspection showed 180 successful
logical model responses, ten byte-score improvements, and no exact matches.

Single component measurements while the live campaign continued:

| Operation | Seconds |
|---|---:|
| Write full 230.83 MB checkpoint on Windows mount | 6.18 |
| Write identical checkpoint on native WSL storage | 5.20 |
| Read checkpoint on Windows mount | 2.46 |
| Read checkpoint on native WSL storage | 2.18 |
| Verify all 16,091 pinned files | 1.81 |
| Rebuild repair queue once | 1.02 |

Both temporary checkpoint variants parsed back to the identical complete state.
These are single observations under contention, not controlled speedup claims.
The controller writes the full checkpoint before and after each item, hashes
inputs before and after execution, and rebuilds the queue multiple times.
The supervisor also reloads/backups state and restarts workers every ten items.
Existing buffered compact JSON is already active; that previous optimization
must not be counted again. Native storage alone saved less than one second per
write here: serialization and state volume deserve priority.

Recommended order:

1. Make detailed semantic traces and candidate history immutable, hash-addressed
   artifacts. Persist a compact scheduling state and a durable per-item journal
   with periodic snapshots. Preserve inflight identity, atomic commit/recovery,
   evidence references, and corrupt-artifact rejection; test crash/replay parity.
   Roughly 12 seconds per item currently goes to the two full writes. Removing
   most of that could save around one-sixth of this sampled end-to-end time;
   this is an opportunity estimate, not an implemented speedup.
2. Reuse one queue projection for selection/status/receipt within an unchanged
   state version. Cache header AST/layout probes with complete compiler, flags,
   include closure and requested-type/global identities. The live worker was
   observed running a header AST probe; repeated probe cost is not yet profiled.
3. Overlap isolated CPU compilation/semantic jobs with one bounded model request
   queue. Start the concurrency experiment at two CPU workers: WSL currently has
   four CPUs and about 8 GB RAM. Workers need private mutable workspaces/DBs,
   and one controller must merge receipts after source/evidence revalidation.
   The existing parallel benchmark has a prepared manifest but no completed
   rounds; there is no measured concurrency speedup yet.
4. Reuse source-and-panel-bound semantic evaluations and compile results when all
   relevant inputs match. Prioritize short counterexample screening before full
   candidate validation, preserving the complete acceptance gates and required
   diagnostics. Measure confirmed improvements per wall-hour as well as throughput.
5. Add stage timers and queue/model wait metrics to locate remaining costs before
   changing model size, output budget, or semantic exploration limits. Those
   changes alter search quality and need paired comparisons.

Service records additionally show a paused event at 1789094241.309361 and next
service start at 1789151431.1051261: about 15.9 hours between those events. Calendar
elapsed time therefore does not represent continuous campaign computation.

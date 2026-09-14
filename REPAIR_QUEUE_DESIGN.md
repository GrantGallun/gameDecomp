# Evidence-directed repair queue (experimental v1)

Implemented in `solver/repair_queue.py`, dispatched by `eval/completion_campaign.py`.
Use `--scheduler evidence-v1` on a NEW campaign, or an explicit `--fork-from`.
The default remains `legacy` for existing experiment drivers and comparable runs.
Resume requires the same scheduler/configuration and frozen inputs. Do not change
a live campaign's policy or edit its frozen code snapshot.

## Authoritative data and derived indexes

| Structure | Representation | Purpose |
| --- | --- | --- |
| Function state | Existing `nodes[name]` map | Source identity, receipts, acceptance state, semantic/byte champions |
| Dependency graph | Sparse caller/callee adjacency lists, SCC member lists | Bidirectional indexing and cycle representation, not a hard eligibility gate |
| Work item | Frozen `WorkItem` record | Function, lane, action, evidence hash, deterministic priority tuple |
| Shared issue | Frozen `SharedIssue` record, keyed by identity hash | Backend/environment issue and affected functions; explicitly non-executable |
| Experiment history | Existing per-node jobs, augmented with lane/evidence hash/model flag | Suppress repeating an action on the same source/evidence |
| Ready queue | Rebuilt heap | Choose work; never a second durable source of truth |
| Checkpoint projection | `repair_queue` JSON map | Inspect pending work and shared issues without parsing model transcripts |

Keep detailed diagnostics in existing receipts; work items contain hashes rather
than copies of assembly, C or execution traces. The projection scans nodes/jobs
and sparse edges, then heapifies ready items. It does not allocate an NxN matrix
or compute all-pairs reachability. Canonical hashing/sorting adds cost proportional
to the evidence serialized. For thousands of functions this simpler rebuild avoids
stale heap entries and complicated invalidation; profile before adding incremental
indexes. Histories can eventually be indexed in SQLite if measured scan costs justify it.

## Dispatch and fairness

- Intake: obtain a candidate with existing machinery.
- Frontend failure: bounded deterministic recovery, then existing model profiles.
- Compiled/frontend-admitted without semantic evidence: zero-model validation visit.
- Observed semantic failure: two distinct counterexample-directed model profiles.
- Sampled pass: existing byte-search profiles, preserving coverage debt and both champions.
- Semantic environment unavailable: deterministic byte search remains eligible;
  no model visits pretending an unavailable execution is a source counterexample.
- Parked backend/operational problem: shared issue, no executable source work.
- Object exact/integrated/function-exact awaiting integration: no further source repair.

Priority is `(visits // 2, lane, -unsolved_direct_callers, instruction_count, name)`.
Two-visit fairness bands prevent continual preference for one lane starving all
others. Caller count is an explicit heuristic, not measured information gain.
Cycles do not block dispatch. Compile sweeps still exclude byte work.

Each action is attempted once per measured evidence key. Source changes, changed
compiler diagnostics, semantic panel/contract key, or counterexamples can reopen
it. Score changes, visit counts, and elapsed time alone cannot. Finite profiles and
the campaign work budget remain; no claim of unbounded convergence is made.
Legacy histories do not have these keys: changing policy requires a fresh/forked
campaign, not opportunistic migration of an in-flight receipt.

## C data structures and shared facts

Scheduler records are NOT recovered game types. Existing target-measured type
constraints and atomic type transactions remain the source of layout hypotheses.
Do not merge two structs because their names, stride or byte score look similar.
A future shared layout/interface fact should identify target/build, ABI, byte
offset, width, alignment, signedness hypothesis, provenance and consumers. Store
conflicting hypotheses separately; a measured access width is not proof of a C type.

This version indexes shared backend/environment problems; it does not yet extract
and propagate a general shared type-fact graph. Caller/callee adjacency supplies
ordering leverage only, not new prompts or automatic verified contracts. A new
fact must actually be consumed and revalidated before it changes acceptance.

## Safety and remaining work

The scheduler neither writes missing backend implementations nor grants execution
contracts. Shared issues are diagnostic records, not proof of a common fix.
Unknown operational failures are kept function-local to avoid false grouping.
Environment-unavailable functions can still reach object exactness through compiler
search. Sampled semantic passes never imply universal equivalence or whole-ROM exactness.
All compiler, frontend, source-hash, frozen-input and integration gates remain.

Next validation is an equal-budget A/B campaign on identical starting candidates,
with isolated databases and frozen policy snapshots. Measure exact objects, semantic
counterexamples cleared, unavailable executions, repeated actions and wall time.
The unit tests and read-only v7 projection validate routing/persistence, not improved
decompilation yield. Keep the current v7 run as the legacy baseline; do not relabel it.

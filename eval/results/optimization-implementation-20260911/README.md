# Throughput implementation and experiments — September 11, 2026

The audited runtime improvements are installed in the existing full-game campaign under revision `20260911-throughput-reuse`. The campaign is temporarily paused for isolated GPU comparisons. This document will record the final experiment decisions and resumed-run observations.

## Installed changes

- Passing semantic reports use an explicit projection of verbose observational lists. Full report hash, execution debt, unknown fields and source/call obligations survive. Failure-directed prompts retain their existing evidence. The client checks a conservative prompt/output headroom estimate before sending; overflow becomes a recorded diagnostic, without silently truncating evidence. This is an estimate, not exact tokenization.
- Worker synchronization copies missing run payloads while retaining complete historical records, cleanup and transactional import semantics.
- Full input verification opens each file once, checks its descriptor is regular and hashes every byte. Timestamp-only validation is not used.
- Identical candidates within a job reuse their existing artifact name through the existing pin-bound compiler cache. Ordinary frontend checks, attribution, exact certification and lineage logging still execute. Cache keys retain filenames, commands, recipes and target inputs.
- Target exploration and stress work have a lossless, checksummed cache bound to all function inputs, seeds, budgets and complete admitted environments. ABI/callee admission and source-specific candidate evaluation remain ordinary work. The codec preserves dictionary order, only decodes explicit result types, and bypasses large traces/payloads. Candidate-influenced target comparison traces are not independently cached.
- Worker processes run up to eight jobs, restoring all runtime wrappers, SQLite authorization and the compiler-recipe cache between jobs. Processes still recycle to bound retained memory.
- Service batches increase from 50 to 200; exit waits respond immediately to process completion. Pause still drains dispatched jobs and prevents new dispatch.
- Controller/import/setup timing is explicit. The dashboard shows live score gains per active hour, target-work hits and controller stages, with overlapping totals clearly identified.

Per-function search budgets, model effort profiles, f16 KV, fixed 32k allocation, model weights, three private workers/two model preparation jobs/one inference slot, and acceptance gates remain unchanged.

## Component measurements

| Check | Before | After | What was preserved |
| --- | ---: | ---: | --- |
| Synchronize 5,094 run metadata rows, 19.44 MB | 1.22–1.33 s | 0.047–0.053 s | Every full historical row/config, foreign-key checks |
| Verify all 16,095 frozen files | 1.68–1.70 s | 1.06–1.08 s | Full content hashes, nonfile/mutation rejection |
| Three identical real MIPS compilations | 0.930 s | 0.468 s | Six frontend checks and six exact checks across arms; identical objects and retained lineage |
| Target panel, updateControllerPakFileDeleteErrorPrompt | 0.678 s cold | 0.331 s warm | Identical full report, identity and 64 cases |
| Target panel, initFallingActionProjectile | 0.776 s cold | 0.546 s warm | Identical full report, identity and 64 cases |

These are bounded component/cold-warm measurements, not a whole-game multiplier. The run-row benchmark isolates metadata, not the entire 1.46 GB campaign DB. Four real jobs alternating functions and private DBs within one process also retained identical cold/warm source hashes, scores, exactness and complete semantic reports. Those worker wall times overlapped unrelated validation, so they are correctness checks rather than controlled throughput estimates.

## Validation and receipts

- Final staged frozen suite: **2,047 passed in 38.76 s**. The frozen code retains its earlier feature set; unrelated main-tree features were not copied into it.
- Main-tree full suite: **2,177 passed in 42.05 s**, followed by the additional bounded-codec case in the final staged suite.
- A real spawned pool completed 40 jobs across six processes with at most eight jobs per process.
- Exception tests cover changed slots/DBs/pins, stale authorizers, unexpected worker failure, raw receipt failure, wrapper restoration and pin invalidation.
- The browser dashboard rendered the new rate and controller panels; the local server was restarted to load the new backend.

[Controller benchmark](controller-benchmark.json), [fresh compilation replay](../optimization-audit-20260911/fresh-compile-replay.json), [target panel benchmark](target-work-benchmark.json), [real worker smoke](worker-smoke.json), [pool recycling](pool_smoke.json), [staged file manifest](staged-manifest.json), [staged tests](staged-tests.log).

## GPU experiments and remaining branches

The paired high/medium/low replay uses 12 saved prompts, identical seeds/schema/content across effort arms, and rotated order. Separate semantic checks compare candidates against the same parent-bound target panel. Eight sampled parents originally lack admitted frontend results, so candidate validity must be distinguished from comparable semantic improvement. The replay evaluates direct proposals before the campaign's later deterministic normalization/recovery. No benchmark candidates are imported.

A separate original/compact prompt comparison targets the two motivating oversized reports. A model-free speculative-decoding backend probe is prepared for exclusive GPU access after the effort replay; it uses the existing local weights and restores Ollama afterward. These experiments do not automatically change production policy.

Whole-panel serialization, cross-filename compilation reuse, target-only comparison-trace caching, and authoritative DB relocation were not deployed. The implemented target-work cache leaves admission/source binding intact, same-job naming retains the build cache's complete key, and incremental row synchronization addresses the measured I/O waste without moving DB authority. Prefix-locality scheduling would need an additional fairness/utilization comparison; observed prefill was much smaller than generation time in the audit sample.

## Operations and rollback

The revision directory `eval/results/resume-pipeline-20260908/revisions/20260911-throughput-reuse` contains the standalone full previous checkpoint, previous code files, previous launch/service settings and an amendment manifest. The current authoritative campaign database and all attempt/proposal history are retained. Roll back only at a drained pause, restore the previous files/config and matching checkpoint/pin contract together; preserve any later imported history rather than blindly overwriting the database.

Resume command uses `eval/campaign_service.py --run ... --batch 200 resume`. The progress dashboard remains at `http://127.0.0.1:8765/`.

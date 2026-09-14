# Compiler and semantic reuse audit — September 11, 2026

Read-only audit of main-tree and active frozen runtime. No live process, candidate, checkpoint, or configuration changed. Main-tree line numbers below unless explicitly marked frozen. Read DESIGN.md and PIPELINE_MAP.md. No relevant AGENTS.md found in source-tree search.

## Measured evidence

Bounded read-only query of active `campaign.sqlite`, attempt IDs **33515–34514** (exactly 1,000 rows):

* 250 rows repeat an earlier `(func_addr, source_sha256)` in this window; 233 of those repeated rows compiled successfully.
* 46 repeats occur within the same `run_id`; 45 compiled successfully.
* All repeated-source groups have identical recorded compilation outcome and score. This does not establish identical build inputs: 40 groups have differing serialized compiler recipes (which include slot-dependent paths).
* Strategies include 229 retained-frontier reverifications, 137 root reverifications, 63 context projections, 19 deterministic reverifications, and 483 deterministic depth-one/two attempts. Duplicate count is **not** a count of safely removable lineage rows or guaranteed compiler-cache hits.
* Checkpoint commit 361: 35 build-cache hits / 1,955 misses; 176 semantic-cache hits / 687 misses; 228 layout-cache hits / 265 misses. Cumulative measured compiler wrapper time was 463.85 s, semantic-call wrapper time 410.46 s, worker time 6,234.63 s. Worker durations overlap; these values cannot be added into a whole-campaign speedup estimate.

## Ranked changes

### 1. Reuse already compiled identical candidates inside a repair job

`eval/agentrepair.py:269` checks retained source against the root only, not against sources already compiled in `initial_states`. Example attempt 34471 context projection and 34472 retained-frontier reverify compiled the same source for `reserveSoundEffectQueueReadIndex` in the same run, with identical compiler recipes. The corresponding active frozen code has the same behavior near lines 255–274.

`solver/repair.py:179` also recompiles the baseline immediately after agentrepair has just compiled the same source. That baseline has no extra DB row when the caller supplied `parent_attempt_id`, so the SQL duplicate counts understate repeated actual compilation.

Safest first implementation: allow deterministic search to accept a freshly verified root attempt + immutable object/artifact reference and explicit input binding; merge retained hypotheses/lineage onto an already freshly verified identical source in the same job. Do not accept a cached score alone. Either record a new attempt with a reuse receipt and the existing verification/artifact hashes, or record the additional parent edge without deleting old attempt rows. Keep ordinary frontend/exact certification for acceptance.

Tests: root source/artifact mutation must invalidate reuse; changed recipe/header/target must invalidate it; a retained candidate matching a projected child preserves both parent histories/hypotheses; distinct candidates with equal scores remain distinct; failed frontend must not become accepted; replay/resume imports still remap all references exactly once. A real MIPS run must compare cold and reused source, allocated object image, frontend, attribution, semantic report and exact certificate.

### 2. Cache target panel construction separately from candidate evaluation

Current `fast_runtime.py:134–153` wraps only `Panel.__call__`. `DeferredPanel` constructs a fresh `Panel` per repair job (`eval/agentrepair.py:283`, `semantic_lane.py:180`), performing ABI/header analysis, concrete-callee admission, target coverage exploration (`semantic_lane.py:270/284`), and stress generation (`:311`) before any evaluation cache can hit. Its final `identity` is calculated only after those computations (`:322`). Existing semantic_seconds does not measure this construction cost.

Add separate construction timing first. Cache a versioned, checksummed serialization of target-led cases, report, ABI/contracts, execution debt and immutable admitted environment. Bind full campaign pins, target object/normalized assembly, compiler target, included-header/source ABI context, function, all budgets, environment and runner version. Keep candidate-dependent source checks and candidate executions fresh. Rebind filesystem receipt paths rather than copying stale worker paths. Failure caching should be restricted to fully bound deterministic failures; missing artifacts can appear later.

Tests: cold/warm panel cases, identity and all debt/report fields are identical; changed header ABI, ROM leaf, global extent, target, runner or budget is a miss; failed deferred initialization retries after artifacts/includes change; no candidate-selected inputs enter the target panel. Profile at least a short function and a target exploration retry function before estimating gains.

### 3. Build-artifact reuse across timestamped candidate filenames

`fast_runtime.py:169` keys successful build results on the entire command, cwd and file paths as well as hashes. Agentrepair root/retained/deterministic tags contain `time_ns()` (`:161/:251/:271`). Thus identical source at another tag misses; other worker slots also miss and have separate cache roots (`fast_campaign._worker`).

Use a canonical content-addressed compilation workspace/input name with explicit provenance, or a narrowly validated artifact mapper. Do not merely remove filenames from the key. Source-attribution artifacts embed paths (`source_attribution.py:24/:128`); the compiler source wrapper only sometimes uses `#line 1 "candidate.c"` (`workspace.py:403–420`), so filename-sensitive code can have semantic differences. Relative includes, `__FILE__`, debug lines, conversion snapshots and diagnostics need preserving. Returned artifacts must remain attached to current source and normalizer/recipe and pass current frontend/exact gates.

Tests: same bytes under two requested names hit without changing attribution; `__FILE__`, relative includes and user `#line` cases decline or behave identically; object/source/debug artifact corruption fails closed; different target and recipe never collide; simultaneous identical requests compute once and each gets complete immutable receipts; old failed artifacts never become a successful cached result. Compare safe canonical compile images before deployment.

### 4. Split machine execution reuse from source-specific semantic reporting

Whole-result semantic cache identity currently includes both source text and full object bytes. Distinct source rewrites producing the same executable image cannot reuse interpreter execution. An execution-layer cache can bind normalized program, linker/data metadata, panel inputs, contracts and runner; then rebuild source object-bound obligations, callee source contracts and operation gradient for each source (`semantic_lane.py:440/:456` and following). Never copy a source-bound report under a new source hash.

Important trap: target execution is not currently independent of candidate context. `mips_differential.compare_programs:1802–1808` builds `_execution_context((target,candidate),...)`; target seed memory includes candidate fallback data. `_execution_context:589` merges symbol names, addresses and max extents; `_seed_memory:682` admits fallback data. A target-only run cache keyed merely by `(panel, case)` can therefore silently change semantics. Bind the complete derived execution context and seeded target memory, or first establish an independently bound canonical context. Preserve the current comparison policy.

Tests must include candidate-only global symbols, changed extents, conflicting linker addresses, initialized data, callee jump tables and aliasing, not only integer leaf examples. This is broader and riskier than the first two improvements.

## Work that should not be skipped

The sampled repeated `local_rewrites` jobs for reserveSoundEffectQueueReadIndex, randomNextObject and __freeParam use different exact source hashes (intact versus metadata-projected source), with distinct evidence keys. pushRaceCourseSurfaceBoundaryWithVelocity also starts its next rewrite from a changed source. `repair_queue.py:82–86` already suppresses repeated profiles under the same evidence key. Skipping by function/profile or equal byte score would remove valid search states. Same source under changed compiler/ABI/semantic evidence may also need revisiting.

The measured duplicate evidence supports targeted reuse and further timing; it does not establish an additional throughput multiplier. No expensive compiler or semantic replay was run during this audit.

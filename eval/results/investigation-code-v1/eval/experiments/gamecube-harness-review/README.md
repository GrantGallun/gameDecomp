# GameCube harness comparison — 2026-09-08

Reviewed upstream commit `cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c` from [Codecaine-AI/gamecube-decomp-harness](https://github.com/Codecaine-AI/gamecube-decomp-harness/tree/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c), downloaded into `.cache/reviews/gamecube-decomp-harness`. Compared against the current gameDecomp working tree, including uncommitted implementation. This is a static implementation/documentation review, not a benchmark or runtime validation. No campaign, solver, or frozen snapshot was changed.

The projects have complementary emphases: theirs coordinates agent work on Melee/PowerPC/MWCC; ours implements reconstruction, repair, and evidence gates for N64/MIPS/IDO. More orchestration features do not establish higher decompilation yield, and more verifier machinery does not establish higher unattended throughput.

## Best ideas to adapt

### 1. Durable job ownership and recovery

Their [job kernel](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/apps/server/src/core/job-queue/kernel.ts) implements idempotent enqueueing, transactional claims, expiring leases, heartbeats, write-fence tokens, revision checks, and expired-job recovery. A recovered job's old worker cannot continue updating job state with its old token. Their separate workflow dispatch lease has different, operator-confirmed stale-recovery semantics; do not conflate it with automatic job reaping.

Our `eval/completion_campaign.py` already has an OS file lock, persisted inflight work, and source/evidence identity checks. Those protect a campaign, but are not a worker-fleet ownership protocol. `CURRENT_HANDOFF.md` records interrupted workers and stale inflight checkpoints, making recovery a concrete concern here.

Adapt a small job/attempt identity and heartbeat layer around the existing campaign, with explicit interrupted/recoverable outcomes. Before concurrent workers, add leases and fence result acceptance/integration as well as job metadata. Do not replace the evidence-directed scheduler with a generic queue: dispatch durability and choosing the right repair are separate concerns.

### 2. Compact target histories that prioritize previous experiments

Their [target cards](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/apps/server/src/core/knowledge-v2/card.ts) offer full/compact/minimal budgets, previous run outcomes, unresolved diagnoses, reusable observations, and integration status. Trimming explicitly favors the target's own history over general fact prose and links. The [target ledger](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/apps/server/src/core/knowledge-v2/views/target-ledger.ts) joins submissions, runs, PRs, and regressions.

We already record attempts and have repair contexts and evidence-keyed retry suppression. The useful addition is one compact projection of those records: source/build identity, hypothesis attempted, observed result, remaining counterexample, semantic/byte champions, and the evidence needed to justify retrying. This is especially relevant to our long, repetitive handoff document. Keep generated narrative separate from mechanical evidence.

### 3. Early translation-unit and source-quality checks

Their [worker micro-gates](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/apps/server/src/core/agent-catalog/agents/running/worker/micro-gates.ts) catch regressions of previously exact non-text sections, new unresolved symbols outside the known link universe, and questionable source changes such as adding static linkage to a known global. Their worker validation compares neighboring functions/sections; their [regression job](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/apps/server/src/core/validation/jobs/regression-check.ts) rebuilds and rejects stale reports.

Our object certificate and isolated ROM integration gate already cover important correctness obligations. Borrow the cheap diagnostics and maintainer-quality checks before expensive integration, rather than adding another terminal exactness oracle. Their gates allow some unavailable checks to remain skipped/tool-unavailable; preserve our explicit evidence debt rather than interpreting an aggregate gate status as universal validation.

### 4. Content-addressed compiler output caching

Their [MWCC cache](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/toolpacks/gamecube-decomp/_impl/gamecube/tools/mwcc_objcache.py) keys objects on compiler/wrapper identity, arguments, source, dependency contents, and include-directory layout, and uses atomic writes. Include-layout identity matters because a newly introduced header can change include resolution even when previously selected headers are unchanged.

Our inspected workspace compile path records source/build provenance and compiler recipes, but does not expose an equivalent persistent dependency-aware object cache. Adapt the design to IDO only after measuring duplicate compilations. Include assembly/postprocessing requirements and avoid caching unsupported builds as successful artifacts. No speedup was measured in this review.

### 5. Operational observability and separate integration outcomes

Their dashboard, event stream, and [worker integration records](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/apps/server/src/core/cycle-runtime/run-state/worker-output-integration.ts) distinguish queued/applying/applied/conflicted/rework outcomes and retain patch/write-set artifacts. Their control surface is substantially more developed than our handoffs and CLI receipts.

A small read-only campaign status view is the immediate transferable part: owner, heartbeat, current stage, last artifact, blocker, and recoverability. Large-scale sandbox/process infrastructure is premature until our smaller campaigns complete reliably.

## Where our current implementation is stronger

1. **Behavioral debugging before exactness.** `solver/mips_differential.py` runs target and candidate MIPS with call/write traces, reports counterexamples and causal slices, and treats unsupported execution as inconclusive. The campaign retains semantic and byte champions separately. No comparable execution-based semantic repair system was found in the inspected upstream core/toolpack code. This is bounded function execution, not a complete console emulator or equivalence proof.
2. **Independent exactness certification.** `solver/workspace.py` derives exactness from `solver/byte_certificate.py`, comparing allocated section contents/layout and relocation expressions rather than relying only on assembly similarity. `eval/integration_gate.py` separately compares a rebuilt ROM in isolation. Their objdiff and regression checks are substantial; our advantage is the explicit independent certificate and separation of function/object/ROM claims, not a claim that their validation is absent or incorrect. Our certificate excludes final link layout and ABI/debug metadata; recorded build inputs are not a hermetic dependency certificate.
3. **Mechanical evidence and retractable hypotheses.** `kb/tms.py` checks model inference citations, follows retraction dependencies, and provides a savepoint ratchet; human/header imports have an explicit exception. Their knowledge-v2 schema also has evidence, digests, confidence, and lifecycle machinery, but models sourced facts about purpose/type/behavior rather than our mechanical evidence-to-inference dependency system. Our API protections are not proof that all callers populate dependencies correctly; broad shared type propagation remains unfinished.
4. **Compiler-checked deterministic reconstruction.** Our target-measured type constraints, ABI-preserving type transactions, and targeted pointer/stack/callback repairs add specialized repair paths. They also have m2c, type/structure tools and a permuter, so this is a difference in integrated repair depth, not exclusive possession of compiler tooling. The scope is compiler-specific and some repairs still need fresh transfer evaluation.
5. **Evaluation separation.** Our reference-teacher exclusion/audit code, frozen fresh cohorts, source-bound receipts, and distinction between reference recovery and autonomous success fit the goal of measuring new reconstruction. Their operational audit explicitly describes successes involving historical source recovery. That is useful project work, but must be labeled assisted/recovered if imported into our experiments.

## What their published audit does and does not show

Their [phase-1 worker audit](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/analysis/worker-audit-2026-09-01/phase1_stats.md) includes 1,627 worker observations, with 107 in its exact cohort and 138 missing event logs retained in the denominator. These are worker observations, not a count of distinct newly solved functions or a comparable autonomous solve rate.

The [paired qualitative rollup](https://github.com/Codecaine-AI/gamecube-decomp-harness/blob/cfc6460e1de02d9d69cd9727ddc83b31b23c8a0c/analysis/worker-audit-2026-09-01/rollups/phase3_rollup.md) offers useful hypotheses: investigate precise mismatches, change helper/lifetime structure when local edits plateau, and switch to section/relocation analysis when the residual changes. It includes counterexamples and notes historical-source recovery and cohort confounding. This supports experiments on our transition policy; it does not prove those techniques cause better results or that simply increasing worker count helps.

## Recommended sequence

1. Produce compact target/campaign status from existing receipts, with explicit interrupted-worker state.
2. Add durable ownership and recovery; require fenced integration before parallel writers.
3. Measure duplicate compilations and evaluate an IDO object cache if warranted.
4. Add missing early symbol/neighbor/source-quality checks to the current integration path.
5. Run frozen, equal-budget comparisons and report unique newly certified objects, semantic failures cleared, unavailable executions, retries, model usage, and wall time separately.

Do not replace our solver with their harness or port MWCC-specific rules as IDO facts. The useful combination is their operational discipline around our existing evidence and repair machinery.

Runtime limitation: their server depends on sibling `Core` packages through Bun `link:` dependencies that are not supplied by this repository clone. The README also contains stale links to old documentation paths. No installation, smoke suite, live agents, or game build was run; nearby tests were inspected as supporting implementation context only.

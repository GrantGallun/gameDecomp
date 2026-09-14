# Ideas

Living queue for Brain Workspace ideas that have not been tested yet.

Update rules:
- Add only actionable or recurring ideas.
- Keep untested hypotheses here until evidence is gathered.
- When an idea is tested, add the result to `hypothesis-graveyard.md` and update or remove the active idea entry.
- Avoid churn; do not log obvious implementation minutiae.

Created: 2026-08-29

## Active Ideas

### IDEA-20260829-01: Evidence-linked hypothesis DAG
- Status: Active
- Source: Current Codex session
- Summary: Replace prose-only revisions and manual reconciliation with scoped claims linked to exact experiment fingerprints, attempt IDs, code revisions, dependencies, contradictions, and invalidation state. Split mechanism, operationalization, and generalization claims so one-function success cannot silently become a general CONFIRMED result.
- Next test: Migrate five high-value entries, including trace-directed-call-repair and bank-reconciliation-needed, then inject one methodological invalidation and verify dependent claims become needs_revalidation.
- Links: None
- Last touched: 2026-08-29

### IDEA-20260829-02: Structured stage-event ledger and failure signatures
- Status: Active
- Source: Current Codex session
- Summary: Persist every pipeline boundary as a typed event with input/output hashes, duration, stable status code, and details. Generate trace and corpus audit views from this ledger, then cluster normalized compiler and object-diff signatures across functions.
- Next test: Instrument one frozen eight-function replay and require tools/trace.py and tools/audit.py to reproduce their findings without parsing human-formatted logs.
- Links: None
- Last touched: 2026-08-29

### IDEA-20260829-03: Prevalence-first cost-aware experiment gate
- Status: Active
- Source: Current Codex session
- Summary: Before spending GPU time, measure whether a proposed detector or repair fires on the corpus, verify the causal surface on stored candidates or compiler microprobes, and rank experiments by expected affected functions and match gain per wall-clock cost.
- Next test: Score the last ten experiments retrospectively and test whether the gate would have killed the no-surface struct-repair run and low-prevalence byte-cast idea before GPU execution.
- Links: None
- Last touched: 2026-08-29

### IDEA-20260829-04: Measure cross-function repair transfer
- Status: Testing - first mechanical register-web family refuted on three parents
- Source: Current Codex session
- Summary: Compiler idioms, shared types, and normalized mismatch signatures discovered on one function should improve other functions with the same mechanically detected shape. Track each ambiguous residual as a target/diff precondition, ranked source-shape levers, positive and negative compile evidence, and compiler fingerprint; measure detector fanout, rewrite applicability, exact closures, and regressions instead of assuming source-text similarity implies transfer.
- Next test: Search a materially different source/dataflow graph on one discovery case; declaration order, initializer splitting, `register`, simple alias lifetimes, increment spellings, and explicit global pointers produced 0/3 improvements across 49 compiling variants. Freeze and transfer-test only after a discovery closure.
- Links: patterns/catalog.py, solver/principles.py, solver/principle_variants.py, HYP-20260902-04, HYP-20260902-05, IDEA-20260902-01, eval/results/principle-agent-ab-v2.json, eval/results/principle-register-web-v2.json
- Last touched: 2026-09-02

### IDEA-20260831-01: Mismatch-only wavefront repair
- Status: Tested - no current activation
- Source: Current Codex session
- Summary: After deterministic validation, give the coding model only the current candidate and violated exact-leaf callsite facts. Reserve one capped repair draw; compile, revalidate, and promote only on byte-exact success.
- Next test: Reactivate only after a zero-token candidate scan finds a genuine normalized exact-leaf violation. Argument and return-flow checks found none across four replayable eligible parents; semantic annotations remain hypotheses and cannot manufacture activation.
- Links: HYP-20260831-01, HYP-20260831-02, WAVEFRONT.md, eval/results/wavefront_token_budget_plan.json
- Last touched: 2026-08-31

### IDEA-20260831-02: ABI-sensitive leaf enrichment
- Status: Tested - parent unlocked, selected ABI edge inactive
- Source: Current Codex session
- Summary: The current exact-leaf pool is dominated by void, pointer, and ordinary 32-bit returns, so completed-callee facts often cannot alter parent codegen. Rank unsolved small leaves by mechanically inferred ABI leverage (narrow or floating return, width-sensitive parameters, consumed return, and unmatched-caller fanout), solve those first, then activate parent work only when the verified signature changes compiled parent assembly or exposes a contract mismatch.
- Next test: Rank return-contract edges by compiler-active consumer shape and exclude subsuming masks. Replicate on a fresh DEV edge where changing a compatible return spelling can alter a wider store, comparison, or extension sequence.
- Links: HYP-20260901-01, HYP-20260901-02, WAVEFRONT.md, eval/results/abi_leaf_pilot.json, eval/results/parent_struct_annotation_pilot.json
- Last touched: 2026-09-01

### IDEA-20260901-01: Two-lane wavefront: exact harvest plus transfer surface
- Status: Tested - harvest succeeded narrowly; transfer target failed
- Source: Current Codex session
- Summary: Separate the immediate exact-count objective from the cross-function-transfer objective. Lane A ranks tiny straight-line DEV leaves by predicted matchability and harvests byte-exact nodes cheaply; Lane B creates compiling baselines only for parents reached by compiler-active exact signatures, then shadow-validates contract propagation before spending repair tokens.
- Next test: Freeze a stratified non-empty straight-line cohort and report exacts by attribution (m2c, header preflight, deterministic repair, model). Choose Lane B only from compiler-active return consumers with a replayable or annotation-buildable parent.
- Links: HYP-20260901-02, WAVEFRONT.md, eval/results/two_lane_wavefront_pilot.json, eval/results/project_header_preflight_replay.json, eval/results/parent_struct_annotation_pilot.json
- Last touched: 2026-09-01

### IDEA-20260901-02: Binary annotation and header dependency closure
- Status: Active - one-parent mechanism confirmed
- Source: Current Codex session
- Summary: Before asking a model to rewrite a noncompiling m2c draft, close its project-header dependencies for the target, direct callees, used type definitions, and declared globals. If private opaque layouts remain, attach target-derived offset/width annotations with explicit provenance while keeping semantic field names retractable.
- Next test: Run the zero-token closure on a frozen cohort of noncompiling m2c parents. Measure compile recovery separately for target header, callee headers, type/global headers, and binary struct annotations; only then budget declaration-only generation for unresolved context.
- Links: HYP-20260901-02, solver/project_headers.py, eval/results/project_header_preflight_replay.json, eval/results/parent_struct_annotation_pilot.json
- Last touched: 2026-09-01

### IDEA-20260901-03: Exactness-first residual dashboard and repair gate
- Status: Active
- Source: Current Codex session
- Summary: Report verifier exactness, text-byte length and positional distance, aligned instruction additions/deletions, relocation differences, and classified structural/layout/register faults beside the weighted progress score. Use those residuals to decide whether a parent is eligible for local edit repair or needs deterministic context closure or full reshaping.
- Next test: Run a zero-model census over unresolved DEV compiled parents, measure score versus raw text-byte distance and structural faults, then freeze a genuinely local panel requiring equal instruction length and a small classified residual.
- Links: None
- Last touched: 2026-09-01

### IDEA-20260901-04: Minimal residual-driven agent repair kernel
- Status: Testing - kernel implemented; efficacy untested
- Source: Current Codex session
- Summary: Center the solver on an agent that observes an exact compiler/object residual, states one source-level hypothesis, applies one bounded edit, recompiles, and branches or reverts from the measured result. Treat byte exactness as the only success condition; keep score and classified residuals diagnostic. Extract a deterministic repair layer only after the same mechanism transfers to multiple frozen unseen functions.
- Next test: Generate roots on the fresh v4 DEV split, select a panel by equal text length and low structural/byte residual rather than weighted score, then compare a strong provider with GPT-OSS under identical call and compile budgets. Require exact closures and unseen transfer; do not promote average-score movement.
- Links: HYP-20260901-05, AGENT_REPAIR.md, eval/sets/sbk1_v4_clean.json, eval/results/agentrepair-updateRacePlayerMode53AerialTrick-smoke.json
- Last touched: 2026-09-01

### IDEA-20260902-01: Bounded tool-using GPT-OSS repair controller
- Status: Testing - search is cached and curiosity activates; efficacy unproven
- Source: Current Codex session
- Summary: Upgrade GPT-OSS from a one-response edit proposer to an observation-action agent. Let it request a small allowlisted tool set—search symbols, read relevant headers/type definitions and prior candidate history, inspect focused assembly/relocation/register diagnostics, propose a bounded patch, compile it, and observe the new residual—while the orchestrator owns filesystem writes, verifier calls, rollback, lineage, budgets, and held-out exclusion.
- Next test: Expose a compiler-backed expression/dataflow-graph experiment tool to the agent and require evidence-driven use. Cached raw-text indexing now removes repeated reads; the first deterministic register-web family compiled 49 variants with no improvements, so more permutations of that family are not the next test.
- Links: HYP-20260902-01, HYP-20260902-02, HYP-20260902-03, HYP-20260902-04, HYP-20260902-05, IDEA-20260901-04, AGENT_REPAIR.md, solver/toolagent.py, solver/principle_variants.py, eval/principle_agent_ab.py, eval/results/principle-agent-ab-v2.json, eval/results/principle-register-web-v2.json
- Last touched: 2026-09-02

### IDEA-20260902-02: Logic-first reconstruction with delayed exactness polish
- Status: Testing - assembly-shape proxy failed direct semantic audit
- Source: Current Codex session
- Summary: Make coherent semantic recovery the primary lane: reconstruct module-level control flow, dataflow, ABI, types, side effects, calls, and behavior while retaining assembly correspondence. Keep byte exactness as a shadow diagnostic, then route only logic-confident functions into source-shape/compiler polishing once shared module context has stabilized.
- Next test: Add value/data-dependency and call-argument checks or differential execution before calling a non-exact rank change semantic progress. The direct finished-source audit found that address/width multiset gains can reward incorrect values and fields.
- Links: HYP-20260902-06, HYP-20260902-10, LOGIC_FIRST.md, solver/logic.py, eval/logic_first.py, eval/sets/logic_first_connected_dev_v1.json, eval/results/logic-first-connected-dev-v1-baseline.json
- Last touched: 2026-09-02

### IDEA-20260902-03: Use the 100 percent decomp as a teacher under explicit exclusion regimes
- Status: Testing - small context signal; raw-source adapter remains weak
- Source: Current Codex session
- Summary: Use the finished SBK1 corpus aggressively for module architecture, source/assembly idiom mining, author and TU style priors, compiler micro-pattern validation, and block-level retrieval. Never expose the selected target body in a prompt; label same-game leave-one-function-out results separately from leave-one-TU-out or clean held-out generalization.
- Next test: Combine the teacher with deterministic project-header/type closure and an identifier/field-to-offset adapter, then require value/dataflow or differential checks before promotion. The v5 machine-shape wins did not establish semantic improvement; body-only splicing froze wrong layouts, LOFO compiled only 1/6 children, and feedback never produced a best candidate.
- Links: HYP-20260902-07, HYP-20260902-08, HYP-20260902-09, HYP-20260902-10, REFERENCE_TEACHER.md, solver/reference_teacher.py, eval/reference_teacher_packets.py, eval/logic_reference_ab.py, eval/results/logic-reference-ab-v5.json
- Last touched: 2026-09-02

### IDEA-20260902-04: Differential function debugger as the semantic oracle
- Status: Testing - debugger works; candidate-source binding and callee effects are now the primary evidence gaps
- Source: User proposal in current Codex session
- Summary: Execute the target MIPS function and the candidate compiled to MIPS from identical seeded ABI register and memory states. Intercept external calls and compare normalized call arguments and deterministic returns, non-stack memory values, return registers, termination, exceptions, and path/instruction counts; emit the first divergent semantic event with register and memory provenance as repair feedback. This is a probabilistic behavioral check, not a proof of equivalence, and byte exactness remains a separate terminal oracle.
- Next test: Before another LLM call, compile an accessor manifest that maps every candidate member/macro expression to its actual offset, width, and signedness. Canonicalize malformed local structs to verified raw-offset accessors, then construct a source-linked dynamic dependence graph for each divergent sink. Cluster multiple sinks by shared source/scaffold ancestors and add recursive exact-callee execution or verified side-effect models before promoting parent passes.
- Links: IDEA-20260902-02, IDEA-20260902-03, IDEA-20260902-05, HYP-20260902-10, HYP-20260902-11, HYP-20260902-12, HYP-20260902-13, HYP-20260902-14, HYP-20260902-17, HYP-20260902-18, HYP-20260902-19, HYP-20260903-20, DIFFERENTIAL_DEBUGGER.md, solver/mips_differential.py, eval/differential_pilot.py, eval/differential_repair_pilot.py, eval/differential_coverage_pilot.py, eval/dag_pipeline_pilot.py, eval/differential_wavefront.py, eval/results/differential-repair-mode16-gptoss-causal-v13-focused.json, eval/results/differential-repair-mode16-gptoss-causal-v18-source-correlated.json, eval/results/differential-coverage-mode16-v3-frozen-panel.json, eval/results/dag-pipeline-census-v2.json, eval/results/differential-wavefront-v2-long.json, eval/results/differential-wavefront-v3-structured.json, eval/results/differential-wavefront-v4-normalized-hard3.json, eval/results/differential-wavefront-v5-resync-preflight.json, eval/results/differential-wavefront-v5-resynchronized-failures.json
- Last touched: 2026-09-03

### IDEA-20260902-05: Monotonic semantic-prefix repair with dynamic backward slices
- Status: Tested - later faults are visible, but string provenance and boundary-only resynchronization do not establish independence
- Source: User proposal in current Codex session
- Summary: After each repair, retain the longest verified semantic prefix as a regression contract while focusing the model on the next divergent event. Continue executing and compiling the complete function, but replace already-equal regions in the prompt with a compact checkpoint summary. For the next mismatch, provide the executed target and candidate instructions that causally produced the differing call argument, store address/value, or branch decision, including concrete input/output register values, loads, call returns, and controlling branches. Re-open earlier regions whenever provenance crosses the checkpoint; matching call names alone are insufficient if call-time memory differs.
- Next test: Replace recursively flattened 240-character provenance strings with node-ID dataflow graphs retaining loads, operations, calls, branches, and source spans. Align graphs at call arguments/writes, find their earliest differing leaves or common candidate ancestor, and use the other sinks as corroborating evidence. For Mode 40, first reconstruct the missing CFG/calls and add FPU execution instead of treating it as a local residual.
- Links: IDEA-20260902-01, IDEA-20260902-02, IDEA-20260902-04, HYP-20260902-13, HYP-20260902-14, HYP-20260902-15, HYP-20260902-18, HYP-20260902-19, HYP-20260903-20, solver/mips_differential.py, eval/differential_repair_pilot.py, eval/differential_wavefront.py, eval/results/differential-repair-mode16-gptoss-causal-v16-exact-sliced.json, eval/results/differential-wavefront-v2-long.json, eval/results/differential-wavefront-v4-normalized-hard3.json, eval/results/differential-wavefront-v5-resynchronized-failures.json
- Last touched: 2026-09-03

### IDEA-20260903-01: Source-linked offset SSA for differential repair
- Status: Testing - detector transfers to Lean/Mode37, but generic text patches cannot enact the repair
- Source: User challenge plus post-hoc comparison of the successful Mode16 trajectory with the five failed resynchronization roots
- Summary: The semantic debugger should report a paired target/candidate dependence graph whose candidate leaves are exact C source expressions and whose memory nodes carry compiler-verified offset, width, and signedness. Multiple divergent sinks should be clustered by shared candidate ancestors, so one malformed accessor or scaffold fact is exposed as a common cause rather than presented as several unrelated assembly symptoms.
- Next test: Add a controller-owned `rebind_access_layer` action. It should accept a typed map of source lvalues to target-observed offset/width/signedness, generate or update raw-offset accessors deterministically, compile the whole candidate, and rerun every semantic case. Test first on frozen Lean and Mode37 v6 roots. Keep missing-CFG Mode40 on a separate `insert_cfg_block` route instead of asking one generic patch schema to cover both mechanisms.
- Links: IDEA-20260901-02, IDEA-20260902-03, IDEA-20260902-04, IDEA-20260902-05, HYP-20260903-21, HYP-20260903-22, HYP-20260903-23, solver/source_layout.py, solver/typedecl.py, solver/memberaccess.py, solver/mips_differential.py, solver/principle_variants.py, eval/probes/mode53_verified_offsets.c, eval/results/differential-wavefront-v6-source-linked-current.json, DIFFERENTIAL_DEBUGGER.md
- Last touched: 2026-09-03

## Candidate Hypotheses

## Open Questions

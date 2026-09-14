# What the decompiler pipeline actually does

Operational inventory, not a roadmap or a capability claim. Last audited:
September 6, 2026. Update this file when a mechanism is added, wired, disabled,
or tested. Historical experiments remain in their original receipts.

Before implementing a new mechanism:

1. Find its owner below and inspect the existing implementation and tests.
2. Check whether the active controller actually calls it. Existence is not wiring.
3. Read a motivating failure receipt. Distinguish no candidate, rejected edit,
   compiler failure, semantic disagreement, byte mismatch, and integration failure.
4. Reuse or extend the owner. Add a test that the mechanism fires on its intended
   input, not just tests that it declines unrelated inputs.
5. Run a bounded, source-bound replay and record its exact scope and result here.
   A new test passing is not a new function solved.

## Entry points: these are different workflows

The resilient worker now defers semantic-panel initialization until a candidate
passes compilation and the strict frontend. Failed initialization is retried when
target artifacts change; a successful panel stays fixed across children. This
prevents a noncompiling root from disabling checks on a repaired child.
Regression validation: 1,198 tests passed, 11 skipped (September 6).
The untouched-cohort wrapper accepts explicit seed and repair budgets and records
them before campaign execution; model-enabled runs preflight the local model.

| Entry point | What actually runs | Status relative to current work |
|---|---|---|
| `eval/completion_campaign.py` | Frozen, resumable DAG intake; resilient compiler/semantic repair; optional isolated integration | Active controller. Latest bounded audit: v14 plus expansion-8-v2, using preserved code snapshot v3; see `eval/experiments/campaign-gap-audit/README.md`. Old checkpoints require explicit forks for changed wiring. |
| `eval/agentrepair.py` | Freshly verify one saved source; deterministic search if requested; bounded local-model repair; retain frontier and receipts | Worker called by the completion campaign. |
| `eval/differential_wavefront.py` | An audited DAG census routes functions to differential repair; noncompiling roots get its own compile intake | Existing separate semantic-first workflow. Not automatically run by completion_campaign. |
| `eval/dag_pipeline_pilot.py` | Build a DAG census and classify differential eligibility/evidence | Admission/audit for the semantic workflow, not a byte-exact solver itself. |
| `eval/frozen_wavefront.py` | Pin inputs and run the differential wavefront experiment | Separate frozen semantic experiment wrapper. |
| `eval/gamewide_batch.py` | Bounded fresh gamewide intake followed by frozen differential batches | Exists separately; not synonymous with the completion campaign. |
| `solver/pipeline.py` | Earlier triage/refinement/permutation/sibling orchestration | Existing alternate controller. Its historical score-band descriptions do not describe the current campaign. |
| `solver/toolagent.py` | Allowlisted model investigation and candidate actions | Separate tool-agent experiments, not the active proposal-only worker. Open-book options are different provenance regimes. |
| `eval/semantic_gradient_beam.py` | Zero-model candidate search over dynamic semantic gradients | Separate controller; not enabled merely by running completion_campaign. |

## Active completion campaign: actual execution order

```text
freeze code/tools/headers/ROM/inventory + lock checkpoint
  -> choose eligible DAG work item fairly
  -> intake or fresh re-verification of saved candidate/alternates
  -> m2c + header-context variants
  -> compile; strict C frontend check; object/relocation verification
  -> if nothing compiles: bounded compile-context recovery variants
  -> retain best candidate + up to three alternatives, with true parents
  -> compile/frontend failure: one zero-model compile_recovery visit per source hash
  -> bounded deterministic or local-model repair on later visits
  -> worker: recover scoped build context; try connected type plans; normalize failed C89 children
  -> compiler/frontend pass: target-led differential panel and causal repair input
  -> retain semantic-best and byte-best separately; bounded stall restart
  -> optional prepared TU replacements in disposable project copy
  -> clean/extract/build; compare complete ROM; archive receipt and ROM
  -> checkpoint: paused, stalled, operational blocker, or cohort result
```

No nonexact candidate is installed in the real game source tree. The working
game's source and assets supply the rest of a combined integration build; an
exact ROM for a replacement subset does not mean the whole game was recovered.

### Intake and compile-context mechanisms

| Mechanism / owner | Input and output | Wiring and limits |
|---|---|---|
| `solver/workspace.py`, `solver/target_intake.py`, `solver/sdk_intake.py` | Binary assembly/object identity -> isolated workspace or explicit unsupported target | Active. Hardware/special assembly targets can remain parked. |
| `solver/m2c_input.py` | Assembly, optional header-only preprocessing -> m2c C draft | Active. Normalizes o32 float aliases on a copy; never edits oracle assembly or imports target function bodies. |
| `m2c_input.draft(no_andor=True)` | Same assembly/header context -> alternative conditional reconstruction | Opt-in experiment only; campaign defaults unchanged. Four game-medium DEV pairs: three identical drafts, one changed draft blocked by the existing do-while guard; no incremental exact matches. See `eval/experiments/decompedia/README.md`. |
| `solver/gfx_packets.py`, `python -m tools.gfx_packet_audit` | Annotated CPU assembly + explicit buffer-pointer symbol/microcode -> constant packet candidates and libgfxd macro hypotheses | Separate CPU-only audit CLI, not campaign source replacement. Reuses CFG/must-dataflow; same-block pairs only, unknown dynamic words retained. Two graphics DEV functions yielded 18 macro instances verified by target-header packet recompilation. No complete graphics function recovered. See `eval/experiments/decompedia/FOLLOWUP.md`. |
| `solver/m2c_context.py` | Header context, typed-pointer assignment hypotheses, extracted literal rodata -> bounded m2c variants | Active. Includes explicit bitcast-to-union candidate lowering. |
| `solver/project_headers.py` | Prototypes, used globals/types/callback values -> include/reconciliation variants | Active. Syntactic header context is not binary evidence. Macro calls must not be mistaken for prototypes. |
| `solver/compile_recovery.py:header_variant` | TU identity + current source -> compatible header candidate | Active on noncompiling intake. SDK TUs exclude game-shim includes; missing internal declarations/types are located in existing headers. Original candidates remain logged. |
| `solver/globaldecl.py` + `compile_recovery.globals_variant` | Symbol addresses + cited access observations + unresolved identifiers -> extern hypotheses | Existing generator newly wired through a narrow campaign adapter. Known included declarations are not overwritten. `extern ?` with no evidence stays unresolved. Access widths do not prove full C object types or extents. |
| `solver/compile_obligations.py:opaque_variant` + `solver/typedecl.py` + `solver/structgen.py` | Fresh dataflow parameter accesses + opaque forward tag -> padded candidate struct | Reuses existing layout rendering. Completes the named tag, not a duplicate typedef. Restricted field-name mappings; no complete header definition is replaced. Object extent beyond observed fields is unknown. |
| `solver/dataflow.py`, `solver/cfg.py` | Target instructions -> control-flow-aware parameter/stack/value identities | Existing analysis reused for the new storage/type feed. Unresolved identities remain unresolved. No evidence-table mutation. |
| `solver/stack_buffers.py` | Unique direct-call argument binding plus target stack spacing -> up to four address-only byte-local array candidates | Active in resilient worker normalization, including compiling nonexact roots; source/assembly hashes and true parents recorded. Frame spacing is a C extent hypothesis, not a proven array. Ambiguous calls, value uses, existing arrays and dynamic frames decline. No function/variable-name rule. |
| Dataflow worklist convergence | Monotone weakening of previously reached block-input facts, preserving the entry boundary on backedges | Prevents repeated loss/reintroduction of a stack-load identity from oscillating forever. Motivated by the untouched __osPfsDeclearPage intake; disputed facts become unknown, never arbitrary loop values. Reduced-loop and invariant/diamond/call tests guard the change. |
| `tools/score_repo_function.py:rewrite_do_while` | Supported `do` loop -> sanctioned for/break form | Reused in intake/recovery. Unsafe continue semantics are declined. This restriction comes from the current build helper. |
| `solver/memberaccess.py` | Offset-named members of byte-pointer parameters -> byte indexing | Exists in m2c_adapter and other paths. Narrow byte-stream case, not a general struct reconstruction solution. |
| `solver/m2c_adapter.py`, `eval/compile_intake.py` | Alternate assembly/header/global adapters -> compiling roots and differential recensus | Existing semantic-wavefront intake. Not wholesale substituted into completion_campaign; supported primitives are reused explicitly. |
| `solver/compilefix.py` | Compiler signatures + draft features -> repair registry/gap report | Existing classification, also used for error diversity. It does not itself execute every registered generator in the current campaign. |

### Repair and verification mechanisms

| Mechanism / owner | What happens | What is not established |
|---|---|---|
| `solver/compiler_recipe.py` | Assignment-only Makefile projection resolves per-TU compiler settings; matching helper adapter preserves guards | Unsupported conditional/postprocessing settings are not silently approximated. |
| `solver/frontend_check.py` | Project default clang C policy after text conversion, source-bound diagnostics and checker identity | Isolated candidate/header acceptance, not full-TU acceptance or semantic proof. Unavailable checker is operational debt. |
| `solver/byte_certificate.py`, `solver/function_boundary.py` | Independent object sections/relocation expressions; optional scoped function-boundary checks | Weighted assembly score is not byte percentage. Boundary exactness alone does not certify the full object or ROM. |
| `solver/residual.py` | Compile state, byte/instruction diagnostics and frontend report -> model packet | An informative packet is not a success verdict. |
| `solver/compile_obligations.py:packet` | Source/assembly hashes, memory accesses, resolved call arguments, relevant included type definitions -> reconstruction input | Candidate views and lexical type-family suggestions remain hypotheses; header types are read-only assisted context. |
| `solver/modelrepair.py` | Local model proposes up to four bounded edits by default; compile/log each accepted edit; retain alternative hypotheses | The explicit type-transaction profile has the larger bounds described below. Failed-edit history includes frontend type errors even when a helper guard masks IDO. Compile-only workers stop at a compiler plus configured frontend pass without relabeling it exact. No unrestricted shell, assembly/header edits, or weakened checking. |
| `solver/type_transaction.py` + modelrepair transaction mode | Included public signature, indexed candidate member/assignment/return sites, existing type/access packet -> up to 64 atomic slot/new edits, 12,000 total old+new characters, 4,000 net growth | Active via campaign `typed_transaction`, prioritized for repeated void-member diagnostics and otherwise available for noncompiling roots. Ordinary signatures are spelling-locked apart from parameter names; missing/complex/conflicting prototypes are explicitly reported as unavailable/ambiguous, not inferred. Rejects new recognized typedef collisions with included headers, preprocessor changes, and changes to the control-keyword sequence. This is not a full C semantic/AST equivalence check. Existing dataflow reports `v0` after return delay slots or `unresolved`; it does not invent C return values. |
| Type-transaction follow-up in `solver/modelrepair.py` | A valid noncompiling child with worse quality -> up to two follow-up depths outside greedy error ranking | Best candidate remains separate; every experiment uses its true parent. Provider calls, depths, and edit limits still bound the run. A remaining exploratory child can occupy one retained frontier slot. This is bounded search, not a claim that every type change deserves preservation. Invalid output does not immediately end this mode while depth/call budget remains. |
| `solver/edit_slots.py` | Physical-line/declaration insertion slots -> controller-bound, hashed source edits | Short `L23` / `DECLARATIONS` names bind to the exact generation parent, not a later draft. Mistaken double-escaped newlines outside C literals are normalized before safety checks. Stale slots, overlaps, whole-file replacements, include changes, assembly escapes, and budget overruns are rejected. Raw proposals remain logged; legacy unique-substring edits remain supported. |
| `solver/repair.py`, `solver/rewrites.py`, `solver/diffrepair.py` | Existing bounded deterministic rewrite/composition searches | Not every search is useful for noncompiling candidates. Instruction-only profiles are skipped for compile/frontend failures. |
| `kb/attempts.py`, workspace/modelrepair logging | Sources, diagnostics, raw proposals, parent edges, model usage, rejected edits | Model proposals never become binary evidence. Consecutive DB rows are not assumed to be a trajectory. |
| Completion campaign frontier | Best plus bounded alternative attempt IDs/source hashes; recompile on reuse/fork | For noncompiling drafts, actual header/total frontend error counts precede weak header-count hints. The model extends the best failed parent sequentially while retaining alternatives. Error count is a search heuristic, not semantic progress. An isolated pass using extra declarations must not preempt an equally exact integration-eligible real-header draft. |
| `eval/prepare_integration.py` | Source-bound exact candidate -> explicitly prepared TU replacement manifest | Conservative: extra shared declarations may remain preparation blockers. |
| `eval/integration_gate.py` | Disposable full project build, complete ROM equality, archived logs/ROM | Subset integration is not all-C decompilation. The real game tree is untouched. |
| Completion campaign integration isolation | Preflight each candidate; bisect failing batches; recheck combined survivor union | An explicit build STOP halts the pass immediately. No assumption that individually passing replacements coexist. |

Object exactness remains `Attempt.exact` / residual `exact`.
`workspace.repair_complete` also requires a configured frontend pass. Never
collapse these into semantic or whole-game correctness.

## Semantic tools already present: do not rebuild them

`solver/mips_differential.py` runs target and candidate with test states and
compares observable behavior. `solver/semantic_gradient.py` extracts causal
load/value/access mismatches; `eval/differential_repair_pilot.py` drives repairs.
`eval/coverage_worker.py` and `eval/semantic_stress_pilot.py` pursue additional
cases and report coverage debt. The differential wavefront dispatches this work
from its census. Consult `DIFFERENTIAL_DEBUGGER.md` and `WAVEFRONT.md` before
changing that lane.

The active completion worker now reuses these primitives through
`eval/semantic_lane.py`; it does not invoke the separate differential wavefront
controller. `--compile-sweep` still selects noncompiling/frontend-rejected work
only, but those workers now evaluate their compiling children when compatible.
Normal campaigns additionally route untested compiling candidates through
`semantic_handoff`. Passing sampled cases, visiting branches, or resynchronizing
traces does not prove all-input equivalence. Coverage debt and unsupported
operations remain explicit.

### Resilient worker wiring (2026-09-05)

Enabled by the completion campaign, or standalone `eval.agentrepair --resilient`.
Legacy standalone mode is unchanged without the flag.

| Owner | Active behavior | Limit / guard |
|---|---|---|
| `solver/repair_context.py` | Project TU metadata supplies existing unconditional includes and recognized balanced legacy-return diagnostic scope; header return spelling recovered when parameter ABI agrees | Header/build-assisted, not binary-only. No reference implementation statement enters candidate or prompt. Unknown/conditional scope declined. |
| `solver/type_constraints.py` | Clang parses included records; the configured target compiler emits measured member offsets/sizes; draft pointer equalities and binary entry-access widths narrow compatible direct-field views; enumerate up to eight remaining plans | Active before model calls on original void-pointer member drafts. Campaign `compile_recovery` now dispatches the resilient worker with zero calls even when model budget is zero; one visit per unchanged source, then model fallback if enabled. Uses existing type-plan actuator, not another source rewriter. Unknown/unsupported layouts decline; union ambiguity remains explicit. Inconsistent means no solution in this header/direct-field dialect, not that the function is impossible. |
| `solver/type_plan.py` | Constraint-selected or model-selected pointer views/member mappings are applied atomically against the current source and checked with offset probes | Narrow pointer/member dialect, not a general C parser. Wrong/missing mappings are rejected. Public ABI and control structure stay protected. Constraint search now supplies plans for the measured alLoadParam case; general type recovery is not established. |
| `solver/compile_obligations.py:header_types` | Keeps a relevant type's dependencies together before expanding unrelated type families | Syntactic header context only; compiler probes check active layout. |
| `solver/modelrepair.py` + existing `solver/c89.py` | Syntax-triggered C89 normalization on roots and failed model children, with separate compiler receipts and actual parent IDs | Initializers remain at their evaluation sites; normalizer is not semantic proof. Zero model calls needed for this step. |
| `solver/m2c_context.py:lower_bitcasts` via `repair_context.normalize` | Lower explicit m2c bit reinterpretations to union expressions at their original evaluation point; narrow integer operands promote to a 32-bit word, supported unary negation/casts stay inside the expression | Reused in fresh m2c intake and resilient recovery of noncompiling roots/children. Never substitute numeric float conversion. Calls, member expressions and side-effecting operands still decline. Compiler/differential verification remains required. |
| `eval/semantic_lane.py` | Existing target-only coverage explorer (5,000 probe budget), retained coverage cases plus stress panel (64-case floor), same frozen cases for every candidate, full-entry differential replay | ABI/callee/coverage debt explicit. Returns, persistent memory and calls checked. Opaque/indirect calls are not concrete callee execution. Every result is non-authoritative finite evidence. |
| `solver/callee_execution.py` + differential runner | ROM-bound, reassembled integer leaf instructions execute with shared caller memory/registers and a shared step budget | Auto-admitted by the active semantic Panel. Single-symbol, relocation-free leaves only; calls, computed jumps, hardware and FPU decline. Actual callee instructions, contents, writes, aliasing and pointer escapes determine behavior; no blanket stack-pointer equality. Frame bounds are necessary checks, not C object bounds. Nested traces are separate from caller coverage and bounded in model feedback. |
| `callee_execution.OutputBuffer` | Explicit non-escaping, address-insensitive, write-only output environment with fixed extent and success code | Opt-in only, never automatically supplied as a binary fact. Checks active frame limits; other stack-pointer arguments decline because aliasing is unresolved. Synthetic payloads are not hardware emulation. Used for the __osBlockSum DEV audit only. |
| Coverage execution obstructions | Retain up to eight unsupported-execution examples even when target-led selection excludes those inputs | Active. All selected cases passing with such an obstruction is `observed_pass_with_execution_debt`, not an unqualified observed pass. Nothing here authorizes semantic/exact promotion. |
| `modelrepair.semantic_prompt` | Behavioral failures lead with counterexamples and existing operation-DAG/causal trace feed, current C and included type definitions | No inferred target source, no weakened gate, no trace resynchronization acceptance. Byte polish remains secondary while behavior fails. |
| Model kernel + campaign retention | Separate byte and semantic champions; source/panel hashes; recompile and rerun on handoff; champions precede incidental beam alternatives | Scores from different panels cannot be mixed. Exact object/frontend checks remain the completion condition. |
| Model kernel stall recovery | Two rejected type plans switch representation; two non-improving depths retry an intact freshly verified initial source with source edits | Same call/depth budgets. Best candidates are not overwritten by restart. This cannot guarantee convergence. |

Receipts and measured failures: `eval/experiments/resilient-repair/README.md`.
The subsequent type-constraint unblock and isolated OSS choice experiment are
in `eval/experiments/type-constraints/README.md`: eight compiling candidates
from original draft 29535 with zero repair-model calls; selected source passes
64 automatic and 580 prior development audit cases, still nonexact. The old
campaign checkpoint and earlier failed trials remain unchanged.
Tests: `tests/test_resilient_repair.py` plus model, agent, campaign, type and
differential suites; `tests/test_type_constraints.py` and worker activation tests
cover the new constraint stage. The historical assisted follow-up below describes the
pre-wiring state; do not read its “not wired” statements as current behavior.

## Running and resuming

The current development panel is `eval/sets/autonomy_wavefront_dev_24_v1.json`.
It is header-assisted, deliberately inspected development data—not heldout
capability evidence. Historical source seeds are explicitly recorded.

Normal campaign: intake, repair, byte polish, optional integration.
`--compile-sweep`: intake/reverification followed only by compile/frontend
failure visits. Each gets a recorded outcome; a work budget can still pause
before all visits. `compile_coverage` lists per-function state and model visits.
A completed compile sweep is not an exactness-completed cohort.

```bash
cd /mnt/c/Code/gameDecomp
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state eval/results/autonomy-wavefront-24-v9.json \
  --fork-from eval/results/autonomy-wavefront-24-v8.json \
  --compile-sweep --max-work-items 26 --model-calls 6 \
  --timeout 420 --num-predict 10000
```

To resume an existing checkpoint, replace `--fork-from ...` with `--resume` and
keep its configuration unchanged. Increase `--max-work-items` for more visits;
add `--integrate` for isolated integration checks. Changed code/tool/model inputs
require an explicit fork. Do not edit old receipts to make them fit new code.

## Measured history and unresolved work

- September 6 follow-up: automatic stack reconstruction fixes the undersized
  __osBlockSum buffer. With real __osSumcalc execution and an explicitly assumed
  hardware-output model, the saved root passes 59/136 and the generated array
  passes 136/136 cases; combined modeled instruction/branch coverage is complete.
  Both remain nonexact (score 81.492). Without the output model the success path
  is explicitly blocked by uninitialized data, not declared correct. Two other
  callers retain 64/64 with actual alCopy/callback-helper effects. The 32-function
  activation audit found three stack-candidate functions and five executable
  leaves used by nine callers; this is not nine recovered functions. See the
  compact handoff and immutable `stack-callee-*.json` receipts in the campaign-gap
  experiment. Campaign v14/expansion checkpoints were not changed or resumed.
- Latest audit: zero-model recovery dispatch reproduces alLoadParam; extended
  bitcast normalization gives alSynSetFXMix 64/64 sampled passes; the dataflow
  fix unblocks a new SDK intake hang. No new exact match. Fresh/replayed 24- and
  additional 8-function counts, stack-pointee debugger controls, and remaining
  cases are in `eval/experiments/campaign-gap-audit/README.md`. General recursive
  callee execution/C-object extent recovery and eager target-panel preparation
  remain gaps; bounded leaf execution is now present as described above.
- v4: 7 object-exact, 4 integrated together, 11 noncompiling, 3 hardware parked.
- v6: 8 object-exact, 6 integrated together, 7 noncompiling, 3 hardware parked.
  `COMPLETION_CAMPAIGN.md` contains receipts, hashes and exact candidate names.
- v9: 8 object-exact, 12 compiling nonexact, 1 noncompiling, 3 hardware parked.
  Six of the original seven compile failures cleared; no new exact match.
  Separate differential audits produced 28 sampled passes across the six, with
  unresolved coverage/callee debt and no authoritative semantic-pass designation.
  `alLoadParam` remains stalled on coordinated types and edit actuation.
- v10/v11: same counts after testing coordinated type transactions. v10's
  31-slot proposal applied but failed compilation; v11's two proposals were
  rejected for existing-header typedef collisions. That historical checkpoint is v11,
  budget-paused, not a newly solved cohort. No worker remains running.
- Audit of the seven compile failures found six had zero model proposals; the
  remaining actor function had two invalid header-typedef edits. This was not
  seven exhausted solver failures.
- This continuation wires compatible headers, cited global declarations,
  opaque-tag candidates, storage/type input, source slots, and compile-sweep
  coverage. Tests: `tests/test_compile_recovery.py`, `tests/test_edit_slots.py`,
  `tests/test_completion_campaign.py`, plus existing model/header/oracle tests.
- Post-change live sweep results and the final resume command are recorded in
  `COMPLETION_CAMPAIGN.md`; this inventory deliberately separates implementation
  from measured outcomes. v9 is now `compile_sweep_stalled`; an unchanged resume
  cannot invent a new strategy. Fork explicitly after engineering a new one.

The follow-up implementation adds the `typed_transaction` profile above.
Tests: `tests/test_type_transaction.py` plus existing model, agent, campaign,
and edit-slot suites. Full suite after this addition: 1,127 passed, 9 skipped.
Its live replays are explicit v10/v11 forks; measured outcomes are recorded in
`COMPLETION_CAMPAIGN.md`, not inferred from the tests. v10 emitted a valid
31-slot transaction but did not compile; its header redefinitions now fail
the new deterministic guard in a receipt replay. Larger editable scope is not
evidence of better reconstruction by itself.

Use the snapshot resume command in the campaign-gap audit for current v14 work.
`COMPLETION_CAMPAIGN.md` also retains historical v11 commands. The earlier
v9 command above is the historical compile-recovery replay, not the current
checkpoint. Do not recreate an existing checkpoint path without `--resume`.

For a standalone development replay, `eval.agentrepair` now accepts
`--type-transaction --compile-only --include-header-context --structured-output`.
Provide an explicit source attempt ID, output receipt, and model/depth budgets.
The campaign selects this mode itself; no function-name-specific trigger is used.

Still not demonstrated: autonomous recovery of every game function; binary-only
recovery of the supplied header information; universal semantic coverage;
complete hardware/special-compiler backends; general shared-declaration
integration; guaranteed repair convergence. A named decline is work to route
or investigate, not evidence that a function is impossible.

### Assisted alLoadParam follow-up (2026-09-05)

The last original compile failure now has a compiling, frontend-passing candidate
and 580/580 passing targeted differential cases. This is **supervisor-assisted
development**, not an autonomous solve or a byte-exact match. Source attempt 29647
(fresh selected reverify 29649) is exported in
`eval/results/alLoadParam-hints-selected-v1.best.c`; weighted score 78.621.
The existing v11 campaign checkpoint has not been changed or promoted.

See `eval/experiments/alLoadParam-hints/README.md` for controlled comparisons,
provenance, selected source, diagnostic scope, tests and all receipts. No official
function body was used; headers and TU diagnostic metadata were explicitly used.
The target TU has a scoped legacy return diagnostic exception missing from the
isolated candidate. Restoring it exposed and fixed a `type_transaction.signature`
bug that misread standalone diagnostic decorators as return-type tokens.

Measured gaps: explicit type maps alone did not solve edit actuation; the existing
`c89.to_c89` normalizer compiled OSS's typed child with zero model calls but is not
wired into `modelrepair.search`; a correct differential return repair reduced
byte-progress score and lost the `best` ranking. Keep semantic and exactness bests
separately when routing this controller. These gaps are documented, **not silently
claimed fixed** by the assisted result. Full suite after the ABI parser fix:
1,128 passed, 9 skipped. The final semantic audit still models `alCopy` as opaque
and tests finite values/alias layouts; it is not authoritative all-input proof.

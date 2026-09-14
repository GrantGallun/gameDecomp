# What the decompiler pipeline actually does

## Stable repair incumbents (September 13)

`modelrepair.search` separates candidate selection from exploration ordering.
After evaluating the root, retained alternatives and normalization candidates,
it initializes the selected candidate from the freshly evaluated root and
replaces it only on a strict `state_quality` improvement across all initial
candidates. Equal-quality alternatives remain in the exploration frontier.
This preserves semantic improvements even with a lower byte score, compiler and
frontend recovery, and byte improvements; it does not turn a tied score into an
exactness claim. The existing compile/object/integration gates remain unchanged.

Previously the first exploration-frontier entry became the selected candidate.
The semantic frontier's tie ordering frequently selected retained sources that
only differed in includes. Source identity changed without measured improvement,
resetting the evidence-directed strategy budgets. The selected source now stays
stable on those ties, allowing the scheduler to advance to an untried strategy
or mark existing strategies exhausted. No historical evidence keys are rewritten.
Audit, paired replay, tests and release receipts:
`eval/results/zero-gain-20260913/`.

## Interrupted checkpoint recovery (September 12)

The fast controller holds `campaign.lock` before calling
`campaign_state.read_for_resume`. Healthy startup uses the normal read-only,
checksum-verified reader. Only SQLite's explicit `SQLITE_READONLY_ROLLBACK`
error with a present journal permits a normal `mode=rw` connection to recover
the interrupted transaction. SQLite performs rollback; the controller checks
database integrity, all selected object hashes, and an unchanged commit pointer,
then records a recovery receipt. It does not select an older checkpoint or retry
a failed save. Other I/O failures and corrupt checkpoints still fail closed.

Dashboard and audit readers remain read-only. Supervisor compact status reads
use the JSON pointer even when old pointers lack health summaries, leaving
recovery to the locked controller. Checkpoint connections close explicitly;
save failures include SQLite's extended error code/name and database path.
Evidence and frozen deployment receipts live in
`eval/results/checkpoint-recovery-fix-20260912/`.

## Repeated regions and explicit helper expansion (September 12)

`eval.inline_regions --run RUN --out NEW_DIRECTORY` freezes a checkpoint pointer
and audits only hash-pinned target assembly. It retains the raw assembly and a
replayable `assemblies.json`; `--assemblies` replays without live database access.
`solver.inline_regions` matches fixed windows of at least eight straight-line
integer instructions with consistent register renaming, preserving constants,
register reuse and relocation symbols. Calls, branches and their delay slots,
stack operations and internal labels split regions. Reports distinguish repeated
windows from complete eligible leaf-helper bodies found inside larger functions.
This is similarity evidence: shared idioms and macros remain possible explanations.
It does not recover original source boundaries or establish equivalence.

The normal byte-repair and semantic-repair prompt builders add at most three
within-caller repeated-region hints for functions of at least 128 instructions.
These use the exact assembly already supplied to the prompt, preserve the failing
semantic objective, and do not alter request admission or model budgets. The
cross-function corpus report is an audit artifact, not automatically imported
helper source or a campaign promotion rule.

`solver.inline_expansion.candidates` supplies a fourth bounded round-robin family
to `code_shapes.candidates`, consumed by the existing differential exactness search.
Each candidate replaces one direct call to a pure integer return-expression helper
whose definition already exists in the candidate source. Parameter and return casts
preserve conversions; argument restrictions, one-use parameters, and grammar checks
decline unsupported effects, control flow and bindings. No `inline` keyword or
additional helper definition is required. The complete caller still goes through
the existing compile, differential and exactness gates; helpers remain in the C
translation unit and object/link authority decides acceptance. This initial operator
does not reconstruct missing multi-statement helpers or expand arbitrary calls.

Release evidence and corpus measurements: `eval/results/inline-regions-20260912/`.
Frozen revision `20260912-inline-regions` was installed at checkpoint 5356 after
2,722 main and 2,461 frozen-suite tests passed; normal service resume followed.
The pinned corpus yielded 1,686 repeated windows across the available set with
382 large functions, but no strict whole-helper witnesses. Live progress checks
are recorded separately in the release's `live-validation.json`.

## Register-allocation signatures, gradient bench and mutation search (September 13; deployed as campaign amendment `20260913-regalloc-search`)

These are not deployed to the frozen campaign. They target pending functions whose only residual fault is register
allocation: 78 at checkpoint 14495. The campaign's model repairs had left these at +-0.

- `solver.regalloc_signature.compare(target_dump, candidate_dump)` aligns instructions on register-free shapes.
  - It classifies each register-only difference as `commutative_swap`, `temp_vs_variable` (ugen t-temporary vs a
    uopt-coloured value), `temp_numbering`, `variable_colour`, `saved_order` or `other_register`.
  - It returns a lexicographic gradient: non-register differences (inserts/deletes counted once), register-differing
    instructions, register operands.
  - `delta(before, after)` gives better/worse/same/exact_shape plus the signatures fixed and introduced.
  - Signatures are observations; their source causes are hypotheses.
- `eval.regalloc_probe probe --cohort C --function F --source A.c ...` is the input/output loop. It compiles in an
  isolated native workspace (about 0.3 s per compile) and prints one JSON line per source: oracle `exact`, gradient,
  signatures, differences and delta against the campaign source.
- `eval.regalloc_probe search` does a beam search over `solver.regalloc_mutations.variants`, ranked by the gradient,
  with plateau moves allowed. It logs every compile. No model, and no campaign state is written.
- Families, each with a fire test on its motivating function in `tests/test_regalloc_mutations.py`:
  - `field_local`: a local caching a struct field becomes the field itself (compound, plain, postfix, prefix-in-use;
    read-cast spelling kept).
  - `struct_copy`: consecutive word copies become a struct assignment.
  - `store_loop`: repeated indexed stores become a while or for loop that IDO unrolls.
  - `guard_before_load`: an early-return guard tests the global before it is copied into a local.
  - `single_use`: inline a single-use local, with its width mask dropped.
  - `local_type`, `commutative`, `const_inline`, `stmt_move`, `decl_order`, plus the existing `inline_temp` and
    `stmt_order`.
- Measured on the preregistered first run (`eval/results/regalloc-20260913/`): commutative swaps were 95% inert
  (IDO canonicalises operand order), and the existing statement-order family made 88% of variants worse. Most closed
  residuals were m2c artefacts (field-caching locals, split struct copies, flattened loops, split pre-increments),
  not allocation priorities.
- More families added while closing the remainder, each with a fire test:
  - `load_modify_store`, `rotated_loop`, `readonly_field_local` with `store_value_local`, `compound_assign` and
    `self_update`;
  - `typed_index` (address and read forms), `symbol_scale` (m2c's double scaling on typed externs, which is also a
    behaviour bug), `negative_scale` and `result_local`;
  - `field_local` gained globals, `+keep_decl` and `+unmasked`.
- Outcome: 74 of 78 object-exact, re-verified from saved sources (`eval/results/regalloc-20260913/closed/index.json`).
  3 are byte-identical but need ROM verification: target-side register-symbol relocations and TU padding nops.
  1 is parked as a stack-layout residual.
- `solver.regalloc_search` holds the shared beam search, which takes any compile callback. Tests:
  `tests/test_regalloc_search.py`.
- Live since checkpoint 16197 (`eval/results/regalloc-deploy-20260913/README.md`):
  - a zero-model `regalloc_search` campaign profile runs the search inside normal work items for register-dominant
    nodes, with unlogged scratch compiles and one logged attempt for an improving result;
  - nodes with at most 2 other faults are scheduled ahead of their visit band;
  - acceptance, certificates, ratchet and integration are unchanged, and nothing is imported.

## Checkpoint store pruning (September 13)

`campaign_state.Store` keeps every commit and every superseded node object. By
2026-09-13 `campaign.state.sqlite` held 14,362 commits (17.7 GB, growing about
3.5 GB/day), while the pointer's commit referenced 0.05 GB. C: filled, the
checkpoint save failed with SQLITE_IOERR_WRITE, and WSL stopped. The failed
transaction rolled back (no journal left; quick_check ok on both DBs).

`python -m eval.campaign_prune RUN --keep-recent 100 [--swap]`:
- keeps the newest N commits, the last commit of each local day, and every commit
  `campaign.json` / `checkpoint.previous.json` names, with ids unchanged so the
  next save continues at `max(id)+1`;
- copies only the objects those commits reference, refusing if any is missing;
- runs integrity_check, hydrates every pointer's state from both stores and
  requires identical bytes;
- then renames the original to `campaign.state.pre-prune-*.sqlite` and never
  deletes it.

It takes no campaign lock: stop the campaign and pause the hourly task first.
Nothing reads non-pointer commits: `campaign_state`, `data_matching`,
`progress_map` and `inline_regions` only use the pointer's commit.

First use: 17.7 GB → 230 MB (102 commits, 4,501 objects), state sha256
`6c6c8800…` verified before and after the swap. Receipt:
`eval/results/resume-pipeline-20260908/campaign-prune/`. Tests:
`tests/test_campaign_prune.py`. Still to do: automatic retention or delta
manifests inside `Store.save`. That is a frozen-code amendment.

## Recorded whole-call replay (September 13, main tree only)

Not deployed to the frozen campaign. Unlike the entry capture below, which
snapshots registers and a few pointees at a function's first instruction, this
records a WHOLE call of one function in the running game and replays candidates
against it.

- Record: `python -m eval.project64_trace --job JOB.json` (Windows Python, the
  pinned portable Project64; job paths use forward slashes). `coverage` jobs
  (`project64_trace_coverage.js`) count JAL/JALR targets reached by attract-mode
  play. `record` jobs (`project64_trace_record.js`) arm hooks at a function's entry
  and drop them on return: entry/exit registers, every RAM and device read/write
  with PC and live type IDs, and each callee's entry arguments, return values and
  writes. Output: `call-N.json` plus a receipt. The runner leaves
  `eval/project64_runner.py` (campaign-pinned) untouched and terminates only its
  own emulator.
- Replay: `solver.trace_replay.replay(record, target_asm, candidate_asm, ...)`.
  Starting memory is every byte read before being written. Callees are not
  executed: recorded v0 and recorded callee writes are applied at the same call
  ordinal. The caller's stack frame (entry sp - 0x10000 through the o32 home slots)
  is excluded. **The original is replayed first**; unless it reproduces the recorded
  non-stack body writes, call sequence, arguments (up to arity) and return
  registers, the recording is `unusable` and judges nothing. Divergences are
  expressed relative to entry registers (`a0+0x38`), and every differing call is
  listed so layout patterns are visible.
- Repair: `eval.agentrepair --resilient --recordings DIR` wraps the semantic panel
  with `eval.trace_panel.Panel` (outermost). Recorded failures become
  `feedback[0]` = `primary_counterexample` in `modelrepair.semantic_prompt`,
  labelled as recorded evidence, and rank first in `semantic_key`. Passing
  recordings add a debt note, never an exactness verdict. On a failure only, the
  panel measures the candidate's own pointer-parameter records with
  `type_constraints.measure(..., records=...)` (target compiler; probes the
  source text before the function definition, never the body; cached on that
  text) and `trace_replay.member_at` spells offsets as the candidate's members:
  `a0+0x38 (&arg0->image2)`. A layout that cannot be measured is reported as
  `recorded_field_names`, and the offsets are still given.
- Pilot/control: `eval.trace_replay_pilot` compiles the campaign's selected C in
  an isolated workspace, replays it, replays the original as a control (must pass),
  and replays a known-wrong mutant of an EXECUTED store (must fail).
- Tests: `tests/test_trace_replay.py` (attribution, type IDs, gate, offset/argument
  divergences fire, frame size is not behaviour, multi-call listing, member
  names), `tests/test_trace_panel.py` (feedback ordering, debt,
  foreign/extent-mismatched recordings never judge, caching, register mapping,
  field-name measurement reuse and reported unavailability, padding tolerance),
  `tests/test_recorded_layout.py` (fires on the image/palette permutation,
  declines), `tests/test_project64_trace.py` (multi validation, organisation,
  audio left enabled), and a duplicate-outcome test in `tests/test_modelrepair.py`.
- Partial credit: each failed replay reports `distance` (differing writes, call
  arguments, callees, missing/extra observables, return registers). The panel's
  `semantic_key` is `[-failed, -total distance, passed, ...]`, so a repair fixing
  half the recorded differences ranks as progress instead of a stall.
- Zero-model member repair: failed replays also report `offset_constraints`
  (an argument or same-width/same-value store that differs only in its offset of
  an entry pointer). `solver.recorded_layout.propose` maps both offsets to the
  candidate's measured members and renames `param->member` uses in the body,
  simultaneously; it declines conflicts, collisions, type/width mismatches and
  non-member offsets with written reasons. `agentrepair` runs up to three rounds
  of it before any model call when `--recordings` is given
  (`_recorded_member_repairs`). Compile, replay and the oracle decide.
- Also changed: `modelrepair._edit_span` names the CURRENT C lines of an ambiguous
  old span and asks for a distinguishing extension; a duplicate proposal is told
  what that exact source already scored and what still differed
  (`modelrepair._outcome`); `agentrepair --exhaust-budget` passes the existing
  search option through.
- Many functions per session: `project64_trace` kind `multi`
  (`project64_trace_multi.js`) arms entry hooks for up to 2,000 functions, nests
  recording frames, samples by `every`, abandons windows past `max_events` /
  `max_window_ms`, disables a function after `max_abandons`, and files calls into
  `<function>/call-N.json`. Every trace session is muted at the OS by
  `eval/project64_mute.ps1` (per-process Windows audio session mute).
  `[Settings] Enable Audio=0` stalls the game and must not be used.
  Triage over all recordings: `eval/results/runtime-trace-20260913/replay_all.py`.
- Limits: only the paths the session exercised; interrupts inside the window are
  counted and caught by the gate only if they touch memory the function uses;
  indirect calls need symbols at their recorded targets. Evidence and runs:
  `eval/results/runtime-trace-20260913/README.md`.

## Automatic runtime entry capture (September 12)

`eval.fast_campaign --runtime-plan MANIFEST` enables a bounded capture sweep at
a drained controller boundary under the campaign lock. The manifest hash is
recorded in the run configuration; changing it requires an explicit amendment.
`eval.campaign_runtime` runs at most two plans per controller session, with
source/evidence-bound retry keys and a recorded refresh cadence (24 hours by
default). This is an additional test-input stage, independent of integration.

Each plan launches a fresh isolated copy of the pinned Project64 assets with
playback volume zero. The fixed exporter reads two paused snapshots; the importer
checks their stability, ROM/code identity and the physical register map. The
runner stops and waits only for its own emulator process. The controller then
compiles the currently selected C in a private native workspace and replays that
candidate against the captured entry. Source, attempt, certificate and frozen
inputs are rechecked before saving results. Validated captures become inputs to
later repair workers; unresolved candidates use the existing combined synthetic
and captured panel. Removed or changed managed plans retire their old inputs.
Runtime captures never grant or remove an exact-match verdict.

The initial manifest has first-entry and nonzero-handle selectors for
`getRelocatableHeapBlockBase`: two observed entries for one integer leaf. The
development sweep compiled selected attempt 36449 and passed both fresh entries
in 11.23 seconds (handle 0 returned `0x80160480`; handle 5 returned `0x801fefb0`).
Its source and exact status were unchanged. This does not establish whole-game
equivalence, full caller state, or support for additional execution instruction
classes. Revision `20260912-runtime-capture` was installed at checkpoint 4972
and enabled at 4973. Live validation at checkpoint 4983 confirms both fresh plans
passed, their bindings remain current, both captures are available to workers,
and both muted emulator processes exited. These are separate fresh live captures;
the development receipts were not substituted for them.

The dashboard's automatic capture card and `/api/runtime` expose capturing,
replaying, passed, failed and unavailable phases, the named function, counts for
the latest capture job, meaningful errors and hash-checked receipt/log links.
Current-pass display requires matching current source/attempt/certificate
bindings and available receipts. The displayed count is not a whole-game total
or a sum of all configured plans. Release and development receipts:
`eval/results/automatic-capture-20260912/README.md` and
`eval/results/automatic-capture-20260912/dev-1789240846188114588/result.json`.
Live campaign checks are in that release directory's `live-validation.json`;
`dashboard-live-validation.json` records the visible card, current binding and
four receipt downloads whose bytes match their indexed SHA256 hashes.

## Automatic isolated integration (September 12)

`eval.fast_campaign --integrate` explicitly enables one bounded sweep per
controller session at a drained boundary under the campaign lock. The default
remains off; existing runs need a recorded runtime-option amendment.
`eval.campaign_integration` selects up to five function-boundary-exact pending
candidates and includes the entire previously integrated union. Existing
`completion_campaign.preflight_integration` / `integrate_candidates` provide
preparation, isolated full-ROM builds and conservative failure isolation.

Promotion additionally rechecks source, attempt, certificate and frozen-input
bindings, prepared/canonical TU hashes, manifest lineage, and the archived ROM's
hash and size. A previous integrated member cannot silently disappear. Results
are checkpointed only after verification; canonical game files remain unchanged.
Source/evidence-bound retry keys suppress unchanged failures without starving
later pending candidates. Running and completed sweeps retain receipts, including
partial successes and explicit operational failures.

The dashboard exposes `/api/integration`, with current source/certificate checks
and hash-checked, run-confined receipt/log links. ROM-verified functions have a
separate map category and do not inflate the object-exact byte total. Historical
success remains visible when a later sweep fails. This does not integrate every
object-exact candidate or establish an all-C program. Release records and live
verification: `eval/results/integration-capture-20260912/README.md`.

## Real Project64 capture pilot (September 12)

`solver.project64_capture.import_export` validates paired paused exports from the
audited Project64 revision `6f7612b` and feeds the existing `runtime_capture`
verification/replay path. The adapter requires the explicit physical MIPS register
map, retains and validates all high halves, and checks stable PC/registers/RAM/code,
the supplied ROM hash/header, entry range and target instruction binding. Export
uses explicit byte-to-hex conversion because Duktape's Buffer API does not provide
Node's hex-encoding behavior. This is a Project64 export, never relabeled as GDB.

The isolated portable emulator reached `getRelocatableHeapBlockBase` at
`0x80043040` with a real handle of zero. Target self-replay passed; a deliberate
return-bit mutation returned `0x80160481` instead of `0x80160480` and failed, with
both executions returning normally. A second natural handle of 5 also passed
self-replay (`0x801fefb0`) and rejected the mutation (`0x801fefb1`). This establishes
two real entries for one integer leaf. It does not capture full caller state: the returned
heap pointer's contents lie outside the selected RAM window. Calls, FPU and
unsupported 64-bit instructions retain their existing restrictions. These manual
pilot cases were not injected into the live campaign, and no new exact match was
claimed. The separately authorized automatic stage above obtains fresh captures.
Adapter tests and repeatable pilot artifacts:
`eval/results/runtime-capture-20260912/README.md`.

## Current residual mining and stack-home repair (September 12)

`eval.residual_patterns --run RUN --out NEW_DIR` audits one checkpoint-selected,
source/address-bound compiled nonexact attempt per function. It retains raw diffs
and the immutable checkpoint reference; read-only attempt queries close in small
batches. `--records .../records.json` replays exact retained input without reading
live databases. Ranking includes distinct functions, overlapping target-byte
totals, raw opcode counts, contiguous n-grams, short replacement signatures and
single-instruction operand families. Hunk/context boundaries and instruction
multiplicity are preserved; register-only text changes are not semantic claims.
Checkpoint3707 supplies1153 usable functions, with668 exact/203 noncompiling
excluded and27 selected attempts lacking a usable instruction diff. All1153
target-assembly baselines are pinned; opcode dialects prevent naive enrichment
ratios. See `eval/results/residual-patterns-20260912/current-v3/README.md`.

The audit's stack-home0x18->0x1c motif occurs in23 functions. The new
`rewrites.stack_home_padding_rewrites` adds one leading4-byte unused volatile
array candidate only for a sole supported integer local and an unambiguous,
otherwise matching single stack-home load/store residual. It rejects explicit
address taking and mixed changes; cheap checks precede alignment. The normal
`propose` API reproduces exact objects/frontend acceptance/64 passing cases on
three related ending callbacks. It fires on7/23 motif candidates; broad transfer
and original-source recovery are not established. Existing gates/budgets remain.
Catalog: `single-local-stack-home-padding`. Full source-bound trials and negative
controls: `eval/results/residual-patterns-20260912/stack-home-v1/README.md`.
Main2543 and frozen2311 tests pass. Runtime installation/status are recorded in
the task's `release/deployment.json` and `release/live-validation.json`.

The integration audit also ran existing preparation/full-ROM machinery on all
five checkpoint3645 boundary-exact pending candidates together. The isolated8MiB
ROM is byte-identical (25.72s), canonical TUs unchanged. No status/source imports
were made in that audit. The subsequent fast-campaign integration wiring is
described above; the integration engine itself already worked. Runtime capture was
then integer-leaf only with no verified emulator capture bridge; the subsequent
real Project64 pilot is described above. See the task's INTEGRATION_AUDIT.md
and runtime-audit.md before proposing duplicate machinery or broader capture scope.

## Impact amendment (September 12)

Follow-up revision `20260912-impact-context` installed at checkpoint3533 after
live monitoring found pre-inference context refusals. Prompt-only projection
retains the primary input/cause/branch operands/passing contrast, full C/assembly,
and binding obligations. Selected nested helper traces and invocation text use
explicit hash references; repeated contract dictionaries use lossless column
tables. Above48000 base-prompt bytes, whole secondary examples may be referenced
instead of embedded. Full raw receipts and the final32K/6000-token guard remain.
Saved replay:19 of28 oversized semantic prompts now fit;9 remain guarded.
Actual-builder training canary processes18522 prompt/1350 generated tokens;
no candidate applied or repair-quality claim. Main2508/frozen2283 tests pass.
See `eval/results/impact-context-20260912/README.md` and its live-validation.json.

Revision `20260912-impact` is installed in the frozen campaign at checkpoint3488,
preserving the prior code and immutable checkpoint reference. It deploys the
branch-context and linked-global seed fixes described below, plus the following:

- Inconclusive differential receipts route to environment repair while retaining
  their prior evidence keys and remaining deterministic search budgets. A saved
  replay moves54 functions/34548bytes and redirects19 queued model-polish jobs;
  all2051 evidence keys and1258 eligible jobs are preserved.
- Four fixed compiler-helper discovery names admit only complete, authenticated
  word-pair multiply/shift instruction streams before target exploration.
  Opaque-call inputs now use opaque trace indices, so concrete helper calls do
  not alter subsequent simulated returns, overrides or output-buffer models.
  The unchanged composeFixedTransformTranslation candidate improves from64
  inconclusive cases to64 passes on identical inputs and target executions.
  This is finite diagnostic evidence; exact-object acceptance is unchanged.
- Stress-panel deduplication recognizes fully shadowed duplicate assignments
  without rewriting raw inputs. The popup replay gains4 distinct inputs and1
  call sequence at the same case/trial budget; instruction/edge coverage is
  unchanged and executed work increases. No speedup is established.
- Accepted exact-byte transitions and inference/worker costs are recorded by
  size and repair profile in `fast_metrics.repair_yield`. The dashboard shows
  size cohorts. Measurement begins at this amendment, with666 exact functions.

Main2504 tests and frozen2279 tests pass. The isolated partial reconstruction
entry-guard workflow also improves, with a real model-generated compiling guard;
all64 cases remain unfinished. That workflow is not imported or enabled in the
campaign. Old experiment receipts retain their original code/panel identities.
Release, recovery references and live checks: `eval/results/impact-20260912/`.
This deployment supersedes the historical "not deployed" notes below for the
branch-context and linked-global fixes only.

## Live whole-program treemap (September 12)

`eval.progress_app` now serves `/api/map` and `/api/function?name=...` through
`progress_map.MapFeed`. It reads immutable checkpoint objects in read-only mode,
validates their hashes, releases SQLite before projecting data, and only reloads
changed function objects. It does not read the campaign attempt database. The map
refreshes every10seconds; the cached projection has a5second minimum refresh.
`progress_map.js` draws deterministic nested treemaps grouped by the existing
inferred code clusters. Target bytes determine area; campaign state determines
exactness colors. High similarity/finite semantic passes never turn a block exact.
Missing sizes stay searchable but are excluded from the area and byte denominator.
Search, status highlighting, cluster zoom, current residuals, recent work items,
and recorded differential coverage are available. Missing coverage stays explicit.
The live localhost:8765 dashboard was restarted independently of the campaign.
All2051 known-size functions render;665 exact blocks occupy65272/675200bytes
(9.7%) at validation. Ten focused tests and browser interaction checks pass.
No campaign worker, acceptance rule, or frozen solver revision was changed.

## Linked-global input replay (September 12)

`mips_differential._seed_memory` now applies the 64 KiB seed-offset ceiling only
to synthetic symbols. Explicit linker-bound globals use the existing larger
extent path, preserving address-keyed aliases, the 8 MiB resource ceiling,
32-bit overflow checks and synthetic/scratch/stack collision checks. This fixes
coverage-generated input replay for the popup's gGameSaveDataBuffer+0x196c0.
The isolated real replay builds64 cases; the unfinished candidate fails all64.
Invalid heap-handle exploration inputs remain noncompleted debt. See
eval/results/partial-reconstruction-20260912/memory-replay.json. Main-tree fix;
the active frozen campaign and old reconstruction pins are unchanged.

## Explicit partial-path reconstruction (September 12)

`eval.reconstruct init/advance` adds an isolated opt-in controller around existing
workspace compilation, full trajectory logging and target-led differential tests.
`solver.partial_reconstruction` binds literal unfinished-region markers to target
CFG blocks and permits one source-region replacement per proposal. Dummy execution
or target entry into still-pending blocks suppresses all behavioral credit for that
case, including joined tails. Raw failures remain visible; previous passing whole
paths cannot regress. Compiling partials occupy their own durable versions rather
than the ordinary modelrepair frontier. All holes removed still requires full
semantic/object/frontend verification. No partial source or history is imported
into the active campaign. See `PARTIAL_RECONSTRUCTION.md` for use and limits.

`branch_context` feeds bounded executed guards/indirect jumps into semantic failure
packets for calls, writes and returns. Same-reason path differences remain distinct;
secondary counterexamples retain paths, with a passing alternative where available.
Source/panel bindings and all existing verdicts are preserved. These main-tree
changes are not automatically copied into the active frozen campaign.

## Call and file ordering (September 12)

`miner.units` groups recorded ELF function ranges into heuristic layout clusters.
It uses range unions for aliases/overlaps and preserves unknown extents as
uncertainty barriers. Padding is a locality hint, not proof of an original file;
reference linker-map TU assignments are used only by validation tests.

For indexed evidence/investigation campaigns, `repair_queue.project` orders by
fair two-visit band, repair lane, runnable callee SCC depth, runnable caller
count, runnable cluster size, cluster address, then instruction count/name.
Recursive groups share a depth. Parked/exhausted/done work cannot make a cluster
look runnable. There is no hard dependency gate or reservation; resource-aware
parallel dispatch can overlap callees and callers. Profile budgets, evidence
keys, compiler/frontend/semantic/exactness gates remain unchanged.

Fresh campaigns derive and fingerprint the index from the inventory. Old
checkpoints without an index preserve prior ordering; no silent resume migration.
The current run was explicitly amended while drained at checkpoint2395, revision
`20260912-decomp-order`, with previous code/pointer archived. All2,051 functions
map to175 clusters. Same-state replay preserves all1,279 eligible jobs and their
profiles/evidence keys; within equal visit/lane bands, caller-before-callee edges
fall43->0 across461 edges. Projection median27->30ms. This measures ordering,
not improved repair yield. Main2,223/frozen2,068 tests pass; evidence validation
has2,842 checkable accesses and zero disagreements. Receipts and runtime status:
`eval/results/decomp-order-20260912/README.md`.

The prior medium-effort amendment and benchmark exact handoff were completed by
Claude after the September11 handoff below. The active run retains fixed32k/f16
and medium only for `reasoned_alternative`. Recovery on September12 restored the
missing dedicated model endpoint before resuming the amended run with200-item
batches; coverage at amendment was664 object-exact functions.

## Additional throughput reuse (September 11)

The existing campaign has revision `20260911-throughput-reuse`: missing-run-row
DB synchronization, single-open full content verification, same-job artifact
names through the existing build cache, and bound target exploration/stress
reuse. Frontend, attribution, semantic and exactness gates remain active.
Worker wrappers/SQLite authorization are scoped and cleaned up; the run uses
eight jobs per process and 200-item service batches. Stage timing now includes
setup/import/controller costs; the dashboard displays current active-hour rates.
Passing semantic reports retain debt/contracts/provenance while omitting verbose
observational lists, and prompt headroom has an explicit conservative estimate.
The fixed 32k allocation and original effort/budget policy remain in place.
Final frozen suite: 2,047 passed; four real MIPS jobs preserve cold/warm sources
and complete semantic reports. Component measurements and GPU experiments:
`eval/results/optimization-implementation-20260911/README.md`.

## Stable GPU model allocation (September 11 throughput follow-up)

Live campaign calls pin context at 32k, preventing full model reloads caused by
alternating adaptive 16k/32k requests. Standalone `llm.generate` remains adaptive
unless explicitly configured. Options are cache-bound and headroom checked.
Worker receipts now retain loading, prefill, generation times and token counts.
Paired DEV replay: 203.6s vs 8.2s, both arms 4/4 compiling. Outputs differ;
this is request throughput under repeated context changes, not overall repair
yield. Details: `eval/results/context-throughput-20260911/README.md`.

## GPU-fed rolling dispatch (September 11 follow-up)

The live run supersedes the two-worker waves described below: three isolated
workers keep two eligible model jobs preparing/queued and one CPU job running;
one GPU inference slot avoids allocating a second KV cache. Results import as
they finish, then slots refill. Model budgets and evidence gates are unchanged.
The dedicated server uses port 11435, Flash Attention, f16 KV, one loaded model.
q8 and parallel inference were measured and not selected; see
`eval/results/gpu-pipeline-20260911/README.md`. The progress UI now reports live
GPU activity, VRAM and power. Runtime revision: `20260911-gpu-pipeline`.

## Local progress UI (September 11)

`eval.progress_app` serves a read-only localhost dashboard; `launch-progress.cmd`
starts/reopens it on port 8765. It polls the compact checkpoint/service summaries
and bounded pipeline log tail, showing coverage, in-flight work, recent results,
timings and cache hits without hydrating the campaign DB. The browser's pause
button freezes only the view. Two backend tests and live Chrome rendering,
refresh, filter and view-pause checks passed. See `CAMPAIGN_PERFORMANCE.md`.

## Current-run performance amendment (September 11)

The existing resume-pipeline campaign now uses `eval.fast_campaign`: two private
workers, one model request lock, original evidence-v1 profiles/budgets and ordered
source/evidence-checked imports. `campaign_workers` keeps complete private KB
history, imports append-only lineage transactionally with ID remapping, and
records idempotency in the same transaction. Dispatched raw results and process
leases support recovery. Scheduling now selects two distinct functions per wave.

`campaign_state` replaces repeated full JSON writes with checksummed immutable
objects and snapshot manifests in `campaign.state.sqlite`; `campaign.json` is a
small atomic commit pointer. All node details remain reconstructible. The service
uses the new reader and compact health summaries. Direct JSON consumers must use
`campaign_state.read` or export a full legacy snapshot. Queue projection is reused
within an unchanged state. `fast_runtime` adds pin-bound layout, semantic and
successful-build artifact caches plus stage/model-wait measurements; frontend
and exactness checks still execute. No model/semantic budget reductions.

Validation: full WSL 2,144 passed; real two-worker MIPS smoke and warm replay retain
identical candidates/verdicts with four layout/six semantic hits. Incremental
checkpoint update measured 0.081s versus prior 6.18s full write; no overall speed
or recovery-yield multiplier claimed. Frozen runtime amendment and prior state
are archived under revisions/20260911-incremental-parallel. Operations, recovery,
reader compatibility and measurement limits: `CAMPAIGN_PERFORMANCE.md`.

## Integrated investigation workflow (September 10)

`completion_campaign --scheduler investigation-v1` now routes bounded
investigation visits through the existing worker and tool agent. New actions
inspect binary evidence/call neighbors, compile self-contained target-compiler
experiments, and record supported competing hypotheses. Inspection calls count
against the model budget; probe compilations count against the tool-agent compile
budget. Behavioral candidates survive the handoff to existing semantic/byte
champion selection. Existing policies and frozen campaigns remain unchanged.

`shared_hypotheses` propagates explanations only across witnessed identical
global addresses, preserves conflicting alternatives and retractions, and feeds
source-repair context/evidence keys. It does not infer universal C layouts or
write model output into the immutable evidence tier. `--capability-tasks` makes
explicitly bound shared issues executable as private implementation-repair
experiments with a failing reproduction and separate regression/transfer checks.
Passing tool experiments produce a candidate code tree for a new frozen fork;
they do not change a live campaign or certify game functions.

`runtime_capture` reads an already-stopped GDB endpoint with an explicit register
map and RAM windows; ROM-bound integer-leaf replay reuses the existing instruction
binder and differential gates. `--runtime-captures` adds checked captures to the
semantic panel. No N64 emulator endpoint was available for live game validation.
`--cleanup-exact` wires existing certificate-preserving cleanup before optional
TU/ROM integration. Full implementation, usage and limits:
`AUTONOMOUS_INVESTIGATION.md`.

Validation: focused WSL tests include real MIPS reassembly and a wrong
candidate control; a private engineering-loop test reproduces/fixes/checks
transfer while preserving its original implementation. Full WSL suite:
2133 passed on the final code, including early-stop and explicit per-call seed
wiring. Real IDO compiler smoke
compiled an independently generated multiply-by-four probe and observed a shift;
binary inspection returned ten observations and three callees. Receipt:
`eval/results/investigation-compiler-smoke-20260910/summary.json`.
The completed two-function local-model pilot uses frozen `investigation-code-v2`:
`eval/results/investigation-pilot-20260910-v2/README.md`. Neither arm became exact
or outperformed its peer; both improved one source through the existing byte-unit
normalizer. Transport failures confound timings. It exposed premature stopping
after missing headers. Current code supplies an explicit investigation question
and requires target evidence/diff inspection before finish. Current pilot code
also passes explicit per-call seeds; v2 only shared the configured base seed.
The original v1 run is marked interrupted after transport timeouts. No improved
decompilation yield or full human replacement is claimed.

## Shared parameter-call byte units (September 10)

`address_units.parameter_call_views` operates without a compiler diagnostic.
It supports typed parameter roots and simple single-assignment pointer aliases,
outer pointer casts, positive/negative constant displacements, commuted additions,
and address-of constant array indexing. The target o32 call argument must equal
the unchanged parameter root plus that literal byte displacement at every target
occurrence of the callee. It declines mutated/escaped/shadowed roots, conditional
bodies, already-byte views, absent or ambiguous call evidence and unsupported
source forms. It does not infer record layouts or pointer safety from ratios.
`numeric_relations` reports deltas and integer multiples; measured-size agreement
is marked only if supplied. Stack deltas are descriptive, not padding recipes.

The shared helper is wired into compile recovery, unconditional model normalization
(with structured attempt metadata), and contextual deterministic repair proposals.
No model calls are added. All candidates still pass existing compiler/frontend,
semantic evaluation where configured, and exactness gates. Existing indexed,
global-pointer, stack-buffer and measured-stride generators remain in use.

`eval.pointer_units_pilot` inventories saved pending sources and tests proposals
in private workspaces/databases. Snapshot: 1,355 pending, 46 proposed, 1,308 declined,
one unavailable (`alEnvmixerPull`: no ordinary function definition). Across all 46:
45 compile, 44 pass frontend, 14 improve score with frontend pass (including three
exact objects); one score regresses and 31 are unchanged. Exact: func_800643B4,
updateMenuSpriteActorDebugControls, initThrownTrailImpactProjectile. These are
failure-enriched exposed DEV results, not held-out or game-wide recovery rates.
Reports: `eval/results/pointer-units-pilot-20260910-v1` and `-v2`.
211 main-tree tests and 107 staged frozen-runtime tests passed. Staged deterministic
search reproduces func_800643B4 in one repair compilation. Runtime amendment details
are recorded under the active run's `revisions/20260910-pointer-call-units`.

## ROM-backed relocation certification (September 10)

`solver/function_boundary.py` now resolves external R_MIPS_32, jal and intact
HI16/LO16 pairs plus bounded references to the function's own .text. Each object's
pairing is preserved and independently resolved; identical relocation sites may
have different pairings only when both reproduce the bound ROM bytes. Raw function
bytes, ELF extents, zero tails, annotation/segment mapping and hashed inputs remain
checked. Allocated data/BSS, orphan/multi-HI groups and unresolved symbols decline.
Legacy-subset receipts retain their replay format; extended certificates use v2.
Object exactness and whole-TU/ROM integration remain separate acceptance scopes.

`eval.recheck_function_boundaries` performs read-only saved-artifact replay with
wrong-symbol, missing-symbol, wrong-address and wrong-ROM-offset controls. Results:
`eval/results/function-boundary-recheck-20260910/validated.json`: 18 previously
pending functions plus five prior function-exact cases certify and revalidate;
the allocated-data debug function declines. All 23 have saved frontend passes;
the report checks those receipts against their compiled source/object hashes.
Frontend checks were not freshly rerun. Tests: 56 passed across boundary, object
certificate and campaign suites. The frozen live campaign has NOT been amended,
and its statuses/counts are unchanged by this replay. Main-tree workspace scoring
already invokes this checker; the frozen runtime requires a separate amendment.

## Three follow-up patch protocols (isolated experiment)

`solver/patch_protocols.py` provides slot-only JSON schemas with actual location
enums, source-hash-bound location selection followed by replacement generation,
and focused retries showing a rejected output plus nearby editable source lines.
All patches still pass the ordinary parser/application limits. Binary paths,
old/new pairs, stale selections, unselected slots, no-ops and escapes are rejected.
These protocols are not enabled in the live campaign or main model search.

`eval.patch_protocol_pilot` tests the three failures remaining in the previous
guidance pilot: gameThreadMain, drawSinglePlayerRaceHud and osPfsFileState.
Each arm has two logical model calls per fixed original parent, 4096 output tokens
per call, matching seeds, and rotated arm order. The two-stage arm spends one call
selecting locations, so equal call caps do not imply equal numbers of code proposals.
Focused retries intentionally have less context. Selection and all source hashes
are saved before generation; valid patches compile in private workspaces/databases.
Application validity, compilation, frontend acceptance and exactness are separate
outcomes. No code integration or weakened verifier is involved.
Receipts: `eval/results/patch-protocol-pilot-20260910-v1/report.json`.
Protocol and existing model-repair tests: 37 passed.
Completed all 18 calls: slots-only produced application-valid patches for 3/3
functions, two-stage 1/3, focused retry 3/3. Only focused retry produced a compiling,
frontend-passing child (HUD), and its score regressed 82.182 -> 79.944. No arm
improved a frontend-passing score or obtained an exact match. Two-stage exhausted
the HUD budget on duplicate selection/incomplete output; osPfsFileState selected
valid locations only on its second call, leaving no code-generation budget.
These are results for these protocols on three selected failures, not a general
rejection of staged generation. See the run's README.md for the full comparison.

## Early patch guidance and loaded-address frontend repair (September 10)

`modelrepair.search(..., early_patch_guidance=True)` prepends declaration/line-slot
examples before the long evidence packet. It preserves old/new support in narrow
mode and skips type-plan prompts; slot-only transactions get slot-only guidance.
Default is false and no frozen campaign prompt changed. All validators stay strict.
`eval.patch_guidance_pilot` replays fixed saved narrow prompts, one paired call per
arm on six unique missing-location failures (historical max proposal id 1212).
Same parents, seeds, model digest, schema and 4096-token budget; alternating order.
Guidance: 3/6 application-valid, 1/6 compiling/frontend-passing, zero exact;
baseline: 0/6 application-valid. This failure-enriched DEV smoke is not an unbiased
model validity rate or proof of broader matching improvement. Receipts and outputs:
`eval/results/patch-guidance-pilot-20260910-v1/report.json`.

`frontend_repair.propose` now recognizes a diagnostic-bound s32 address load plus
constant byte displacement passed to a header-declared pointer. It requires o32,
one matching target call with a four-byte load from the unchanged first parameter,
exact matching field/step, and a unique header signature. The conversion wraps the
entire sum, preserving byte units. Missing/wrong evidence declines. Existing model
normalization calls this module; frozen campaign code does not contain this addition.
Fresh general-rule replay recovers `func_80064414`: previously object-exact but
frontend-invalid, now object-exact with frontend pass. No integration performed.
The separate `fadeOutAllMusicSequences` header hypothesis failed compilation and
remains rejected. `eval/results/frontend-exact-pilot-20260910` retains both outcomes.

## Plateau exploration (opt-in; isolated DEV evaluation)

`repair.search(..., plateau_config=plateau.Config())` selects the new controller
in `solver/plateau.py`; omitting it preserves the existing deterministic beam.
The existing beam already accepts lower-scoring intermediates and diversifies
rewrite families. The new controller changes attempt allocation: two compilations
per parent visit, interleaved rewrite families, a twelve-state pending pool, and
four non-improving attempts before an exploration episode. Residual/source-token
identities and visit counts select underexplored parents; bounded lookahead follows
children even when scores fall. The score champion stays separate from the pool.
The same source is never compiled twice within search, and total candidate/depth
limits still apply. No model, new rewrite family, or global convergence guarantee.

Exploration requires frontend-passing candidates; exact completion additionally
requires a source-bound allocated-object-section certificate. No source is integrated.
The active frozen campaign does not enable this new controller. Source/panel-based
semantic champions in the model worker are unchanged; this experiment measures byte
repair, not semantic improvement or readable-source recovery.

`python -m eval.plateau_pilot --output NEW_DIR --count 6 --budget 48` runs paired
private-workspace/private-database searches under WSL. Selection is score >=95 and
<100, pending, at least three historical jobs, and unchanged scores across the last
two jobs. Selection/source hashes are saved before either arm runs. Both strategies
get the same depth and candidate cap; original verification and final champion
reverification are outside the cap. `--min-proposals 4` selects a separate cohort
with at least four distinct existing rewrites, without using experiment outcomes.
Reports retain no-proposal and frontend-blocked cases. These are exposed development
experiments with assisted headers/bootstrap drafts, not held-out game-wide results.

Focused controller, entrypoint, legacy repair and rewrite tests: 86 passed.
Paired results live under `eval/results/plateau-pilot-20260910-v1` and `-v2`.
Both cohorts completed: ten distinct stalled functions (two overlap), zero new
exact matches and no score gains in the new mode. `initFallingActionProjectile`
reached depth six versus depth two under the existing beam at the same 48-candidate
cap, without improving its 99.127 score. Three cases had no available rewrite;
two distinct cases failed frontend checks and were declined by the new mode.
The baseline's 97.889 -> 98.0 on `updateRaceCoursePropModels` still fails frontend
and is not verified recovery. See `eval/results/plateau-pilot-20260910-README.md`.

## Opt-in readability cleanup (not enabled in the campaign)

`solver/readability.py` proposes dead pointer-copy removal and type-derived names
for generated pointer locals. Renaming declines nested scopes and ambiguous member
or label uses; preprocessing in the body declines cleanup. These are hypotheses,
not semantic guarantees. No full-function model rewrite is involved.

`python -m eval.clean_candidate --source candidate.c --function FUNCTION --output NEW_DIR`
only previews proposals. `best.c` remains the original in preview mode. To compile
later under WSL, add `--verify --repo /home/grant/decomp/sbk1 --baseline-db PATH`.
The verifier copies the function workspace and SQLite database into the output
directory, uses the existing compiler toolchain, and archives each evaluation.
Shared repository/toolchain paths are symlinked as in the existing isolated pilots.

The original must first pass fresh frontend and allocated-object-section checks.
Each retained edit requires the same target, scope and recorded build inputs, plus
a fresh source-bound exact certificate and frontend pass. A score of 100, semantic
test pass, or function-boundary-only certificate is insufficient. Edits are tried
one at a time (default 12 attempts); failures retain the last verified source.
`report.json`, `original.c`, `best.c` and private receipts support later review.
This scope excludes whole-ROM/final-link verification and is not a hermetic build.
No automatic source integration, campaign deployment, or readability gain is claimed.
Candidate/toolchain evaluation of this new stage is deferred at the user's request.

## Buffered compact checkpoints

`agentrepair._atomic_json` and the persistent service use a 1 MiB output buffer
and compact JSON. Per-item saves, atomic replacement, contents, verification,
and test budgets are preserved. On the same 2,051-node campaign checkpoint,
the original writer took 15.111 seconds for 276.6 MB; buffered compact output
took 3.153 seconds for 133.0 MB and parsed to exactly equal data. This is a
checkpoint microbenchmark, not a whole-pipeline speedup claim. Receipts are in
`eval/results/checkpoint-speed-20260909/before-after.json`.

## Persistent campaign service

`eval/campaign_service.py` launches the frozen campaign with ten work items per
process, retaining per-item checkpoints and receipts plus a validated previous
checkpoint. Successful budget boundaries recycle the process; three consecutive
worker failures request repair. Input changes remain blocking. `service.pause`
is checked between work items and survives scheduler invocations. The Windows
hourly task records health, starts missing workers, and requests a sandboxed
Codex repair for persistent errors or a checkpoint stale for two hours. Repairs
still require validation and recorded runtime amendments. See the run's
`OPERATIONS.md` for controls, limits, and recovery details.

## Single-precision overflow during differential execution

`mips_differential._float32_bits` translates host float packing overflow into
an unsupported execution result. FCSR rounding/traps remain unmodeled, so these
cases stay inconclusive rather than terminating the campaign or becoming passes.
The September 8 campaign resumed with this one-file amendment on September 9;
its original checkpoint, interpreter, and before/after hashes are archived under
`eval/results/resume-pipeline-20260908/revisions/20260909-float32-overflow`.

## MIPS III/o32 runtime-helper recovery

`compiler_recipe` now admits the exact project `-mips3 -32` configuration;
bare MIPS III and unmeasured ABI/ISA combinations still decline. A real IDO
probe verifies 32-bit pointers/long and 64-bit long long with the expected
wide multiply instruction stream. Existing build guards and object certificates
remain authoritative.

`compile_recovery.variants` calls `wide_runtime_interfaces.reconstruct_helper`
after target o32 and compiler-recipe checks. Entire instruction streams select
multiplication, signed/unsigned division, unsigned remainder, and three shifts;
the generated C is scored through the normal compiler/frontend/object gates.
Names do not decide operations: `__ll_rem` has an unsigned remainder stream.
No runtime interpreter admission or undefined-input semantic claim is added.

Isolated live-toolchain replay `eval/results/resume-blockers-mips3-v3/report.json`
recovers eight of ten `ll.o` helpers as exact objects with frontend passes.
`__ull_divremi` and `__ll_mod` remain draft-compilation failures. The original
game-wide run keeps its frozen code, inputs, and results; these are separate
development repairs, eligible for a later explicit fork/replay.

## Evidence-directed orchestration (opt-in)

`completion_campaign --scheduler evidence-v1` uses `solver/repair_queue.py`.
Typed work items, sparse bidirectional adjacency/SCCs, shared backend/environment
issue records and evidence-keyed job history are persisted with the existing
checkpoint. A rebuilt heap chooses blocker-specific work with fairness bands.
Semantic failures route to counterexample profiles; unavailable runtime cases
retain deterministic byte search without model retries. Existing acceptance gates
and semantic/byte champions are unchanged. Legacy remains selectable/default;
no frozen v7 files were changed. See `REPAIR_QUEUE_DESIGN.md` for schemas, limits,
C-layout fact design, and the pending equal-budget comparison. This does not yet
execute shared capability repairs or propagate a general shared type-fact graph.

## Target-byte record cursor steps

`pointer_stride_repair` compares a generated record-pointer increment with measured
record size and target global-seeded loop stride. It emits explicit byte arithmetic
and tracks cursor spill/reload across calls. Normal compiler and semantic gates
still apply. ItemEffectUse replay v2 now reports64 sampled passes with execution
debt, versus52pass12fail before repair;533positionalbytes still differ.
Freshv7 freezes this machinery and enables bounded GPT-OSS calls. Interruptedv6
batch2 remains separate and must not be reported as a completed run.

## Single wide-parameter reconstruction

`wide_parameter_repair` restores a header-declared 64-bit parameter from named
high/low word arguments on confirmed big-endian o32 targets. Included-header
typedef chains must uniquely identify a long-long type; public ABI and local-name
checks apply. Existing body expressions keep high/low working locals. The timer
candidate is recognized; its replay remains pending until fresh batch2 completes.

## Callback views in expression calls

The existing o32 one-pointer callback view now recognizes bare function names,
expression calls and anonymous expected callback types. Repeated uses require
matching source/target multiplicity and ordered callback-address witnesses.
ItemEffectUse replayv3 passes compiler/frontend; semantic52pass12fail. The first
failure is a double-scaled RacePlayer cursor increment, not a callback mismatch.
No universal callback compatibility or semantic correctness is claimed.

## Frontend-rejected candidates enter compile recovery

The interactive zero-model repair entry point now invokes compile recovery when
the configured frontend rejects a candidate even if IDO compiled it. Missing
pointer-returning callback-wrapper declarations can be reconstructed locally
from bounded callee assembly/header inference while preserving all call slots.
ItemEffectUse now reaches callback-type diagnostics; its frontend is not yet
unblocked. Existing callback-view recovery needs broader call-syntax coverage.

## Incoming stack arguments and derived byte cursors

Dataflow supports opt-in o32 incoming word slots for arguments5–8. Void-field
recovery uses them only for known single-word parameter prefixes; wide/unknown
layouts decline. Single-assignment explicit byte-pointer chains retain their
parameter-relative offsets, matched against target access widths.
Post-fresh `_saveBuffer` replay v2 passes compiler/frontend. Semantic execution
is unavailable and309positionalbytes differ. Frozen-v6 outcomes remain separate.

## Structured object-backend requirements

Unsupported postprocessing still stops compilation. `ObjectBackendRequired`
now retains the resolved command, target, assembler flags and build hashes;
the campaign stores this evidence and the audit routes it to the compiler
backend. No postprocessing command is executed. Fresh-v6 save-menu exposed a
zero-tail trim/weak-symbol rewrite requirement. Isolated candidate applicability
and symbol containment checks are still needed before implementing that backend.

## Automatic indexed workspace hypotheses

`stack_workspace_hints` combines measured header-array capacity, a source loop's
counter/stride, and target stack-address/halfword-store witnesses. It emits a
bounded primitive-array hypothesis, redrafts through m2c, checks the public ABI,
then reuses byte-field and stack-array recovery. It does not prove runtime bounds,
source-to-register correspondence, or pointer increment semantics.
Frozen collision replay `v5-auto-workspace-collision-replay-v1` best1648 now passes
compiler/frontend. Inventoryv64 clears all31 observed frontend cases. Semantic
validation remains unavailable; fresh transfer testing is still required.

## Hinted stack experiment reaches the compiler

The adapter now materializes missing used stack-array declarations, declining
conflicting declarations. `stack_hint_probe --valid-syntax --repair` lowers field
macros and runs the normal zero-model repair gates without integration.
Collision probe v3 best1637 compiles with no compiler diagnostics. Semantic checks
are unavailable: no target trial completed. This is a manually supplied extent
hypothesis, NOT an automatic frontend unblock. Next: derive the hint from recorded
loop/header/target evidence and replay the original frozen checkpoint.

## Experimental stack-layout hint transport

`m2c_input.draft(stack_variables=...)` accepts only bounded primitive array
hypotheses, with fixed-frame containment, alignment, disjointness and target
address checks. It emits m2c stack-template metadata, not reference function C.
Extent remains caller-supplied evidence debt; the API is not an automatic repair.
`stack_hint_probe` saves immutable draft-only receipts with target/header hashes.
Collision probe v1 removes six unknown pointer types, but output still needs
the hinted local declaration and byte-unit normalization. No frontend pass yet.

## Header-sized matrix cursor aliases

`stack_interface_arrays` now accepts same-element local pointer seeds and endpoint
comparisons into its recovered array when target stack-address witnesses exist.
Scalar aliases and cursor addresses share the same storage. Unwitnessed addresses,
wrong element types and external address destinations decline. This is a source
reconstruction hypothesis, not a proof of cursor arithmetic or runtime behavior.
Collision replay `v5-matrix-cursors-collision-replay-v1` recovers `Mat3x3 spB0`;
the separate unknown indexed workspace still blocks compilation.30/31 unchanged.

## Interface-only redrafts survive the reconstruction gate

`compile_recovery.byteview_redraft` now retains a bounded callee-interface redraft
even when there are no byte-field rewrites. The existing public-ABI checks and
ordinary compiler/semantic gates remain. The course-details failure was discarded
after the sound interface had already been recovered; no new solver was needed.
Replay `v5-sound-interface-menu-replay-v2` best1604/source
`76b5804c2b117057b113604310f530fad3e1f638246d4395153d7aba9e696538`
passes compiler/frontend. Semantic20pass5fail39inconclusive; first candidate-only
fault is a double-scaled RaceCamera pointer increment.636positionalbytesdifferent.
Inventoryv63:30/31frontendcasescleared, collisionworkspace remains.

## Pointer-table loads used directly as call arguments

`frontend_repair` reuses indexed-address witnesses to repair scalar-global-indexed
table entries passed to pointer parameters. Index width/signedness, byte stride,
target load destination and preservation through the call delay slot constrain
the candidate. The rewrite keeps byte addressing and loads a pointer word; it
does not establish table extent or semantic equivalence.

Record-settings replay `v5-pointer-table-records-replay-v1`, best1600/source
`c3f74bccdc5f0cdfe71d0628240d09e126e6e0d9ac29ca5505d2745101c1dcf7`,
passes compiler/frontend;1314positionalbytesdifferent. Semantic validation is
unavailable because synthetic asset handles cause target allocator-table faults.
Two signed-shift warnings remain. Inventoryv62:29/31frontendcasescleared.

## Volatile register source views

`hardware_environment.register_views` now converts closed scalar register externs
to volatile word lvalues using encoded virtual addresses, not header physical
address macros. Target range, alignment, scalar use and source identity gates
apply. Address tracking preserves branch delay-slot accesses before clearing
state and retains independent register calculations. Compile recovery applies
the pass to header candidates and fresh redrafts. Device modeling is unchanged.

DMA replay `v5-register-views-dma-replay-v2` best1595/source
`2f3319a66fdaa19d3e3d46fc6bf20620bcff5dc3015db1787a0de4087ee271d3`
passes compiler/frontend;441positionalbytesdifferent. Semantic execution remains
unavailable with explicit hardware-register-environment-required evidence.

## Original-encoding hardware address evidence

`hardware_environment.encoded_register_addresses` reads original encoded LUI and
word-memory instructions, checks textual register/opcode agreement, invalidates
clobbered or uncertain state and rejects conflicting symbol addresses. It is a
read-only evidence feed, not yet source recovery or device emulation. The DMA
target's status address is0xA4600010, whereas its header macro is0x04600010;
blind header substitution is therefore invalid. Nine register addresses are
currently recovered. Remaining frontend work must preserve encoded addresses
and volatile accesses without claiming a RAM-backed hardware environment.

## Fresh-v5 accounting

Both frozen batches completed:12functions,3objectexact,6compiler/frontend
failures,2differential disagreements,1semantic-unavailable,zero modelcalls.
`fresh-v5-complete-audit-v1.json` preserves all original outcomes and routes.
The inventory accepts explicit campaign versions and binds matching replay
receipts; v60 includes v3/v4/v5:31originalfrontendblockers,27cleared,4remaining.
The two post-v5 unblocks are _filterBuffer and osPfsNumFiles, not clean-frozen
successes. Collision's current replay clears widehelper/matrix declarations but
still needs a shared indexed matrix-workspace/alias reconstruction at sp110.

## Call-bound stack read buffers

`stack_read_buffers.propose` joins a scalar-byte call buffer and missing post-call
stack aliases into aligned byte storage. A unique target call argument, fixed
frame, neighboring declared/observed slot, and unambiguous target load widths
constrain the proposal. Read expressions preserve big-endian word/halfword values;
writes and address escapes decline. Neighbor spacing is not proof of allocation
extent, lifetime or callee effects, so ordinary compiler/runtime gates remain.

`v5-stack-read-pfs-replay-v1` best1571/source
`9ea0e50d3922976fe3829b83a1695711c090bfcfbf11423a02cc31a9ee70911a`
clears osPfsNumFiles compiler/frontend.64samplepasses cover80/80instructions and
16/18branch edges, but opaque calls/stack-output effects limit semantic evidence.
154positionalbytes differ; no exactness claim. Frozen fresh-v5 remains unchanged.

## Call-load selection with repeated callees

The existing pointer-word-load repair now selects a unique target call by its
loaded argument identity (parameter root, offset and width), rather than requiring
the callee to occur only once. Two matching calls still decline. This completes
the post-fresh-v5 `_filterBuffer` frontend repair alongside later-parameter field
recovery. Replay `v5-parameter-load-filter-replay-v1` best1569/source
`72cb5390c23df97aaf1b305fee54520bd72ee00013bf7eb38ab54b7a2fc3d2e0`
passes compiler/frontend and64sampled semantic cases,45/45targetinstructions.
Opaque callee/input coverage debt remains;104positionalbytesdifferent,NOTexact.
Frozen batch1 audit preserves its original three frontend failures separately
from this exposed-source repair; batch2 remains in progress.

## Later pointer parameters in void-field recovery

`void_field_repair` now binds later argument registers only when the preceding
parameters have unambiguous one-word integer/pointer declarations. Wide, float,
aggregate and unknown-width prefixes decline. Resolved parameter-relative offsets
include address-temporary arithmetic, not merely the memory instruction literal.
The fresh-v5 `_filterBuffer` receipt reproduces five newly recognized output fields;
this is a post-snapshot proposal test, not yet a compiler unblock. Its loaded-word
argument to osVirtualToPhysical remains an integer/pointer mismatch.

## Header-array interfaces reconstruct stack objects

Empty old-style parameter lists decline without indexing a missing type token;
covered by a regression. This guard was added after fresh-v5's immutable snapshot
and must not be attributed to that frozen batch's code.

`stack_interface_arrays.recover` collects included-header primitive array typedefs
and unique direct call parameter declarations. Array-pointer parameters decline.
Target stack call addresses and matching-width alias accesses constrain scalar
stack roots becoming array objects, with split aliases mapped to elements and
call arguments changed to array decay. Header hashes and source/assembly hashes
are retained; syntactic declarations and storage lifetime remain hypotheses for
compiler/runtime adjudication. The recovery runs after shared word arrays.

Frozen ground replay `v4-stack-interface-ground-replay-v2` best1554/source
`7f1bcc2498673c44373652af77a6aa216074053012a8db1dd01dbe895a93eb9a`
passes compiler/frontend. Inventoryv59 clears25/25 original frontend cases.
NOTexact:5116positionalbytes different, weightedscore27.867. Semantic validation
unavailable: five target trials stop on an uninitialized callee stack read in
multiplyFixedMatrix3s. Scalar Vec3i storage used as an array remains known debt.
Final regression1809passed22skipped. Fresh validation is not yet complete.

## Shared stack-word arrays after loop normalization

Named first-element address anchors are now supported too: a source
`&(&spHEX[0])[index]` with matching bounded loop and target stack-base-plus-scaled
index stores can recover the same shared object. Repeated stores in one loop
share an extent witness. Storage is a union of signed/unsigned arrays, not an
array of scalar unions, so pointer indexing operates within an actual C array.
Frozen `v4-stack-anchor-ground-replay-v1` candidate1520/source
`fd3fe45d328bb5d8c68312d46d364a5ad68c6eabed0c9b26c326e497d270c5d5`
clears the B0 array aliases; missing matrix halfword aliases remain. Inventoryv58
stays24/25. Final regression1807passed22skipped; no semantic/exact acceptance.

`stack_word_arrays.propose` reconstructs explicit indexed raw-stack word stores
when source init/step/bound and target shift/stack-add/store/backbranch witnesses
agree. Fixed frame, word alignment, nonoverlap and closed scalar alias uses are
required. Existing s32/u32 alias types select union word views; missing aliases
require target word loads, with signedness still a hypothesis. This is storage
reconstruction, not semantic proof. Register spelling and assembly metadata have
regressions. `compile_recovery.variants` runs it after do-while lowering and
prioritizes repaired children before its candidate cap while retaining originals.

Frozen `v4-stack-word-ground-replay-v3` emits candidate1499, then normalized1506
(`e64894827faec1cbed0c9c1ea84af3b8c0e1d2dd443ca1d3c3fdc7a72b2f5b46`). Two
six-word arrays at0x80/0x98 replace twelve split aliases. Compiler diagnostics
confirm these errors clear, but B0-array and matrix errors remain. Best-source
ranking retains the older candidate; inspect these source-bound attempt receipts
for partial progress. Inventoryv57 remains24/25, with no semantic/exact promotion.

## Target-witnessed named call addresses

`call_address_globals.propose`, invoked by `compile_recovery.globals_variant`,
recovers an incomplete byte-array view for an unknown global used only as a
direct call address. Independent symbol identity is required. Each call argument
must match a distinct target call/argument witness with the same byte offset and
literal index stride. The recognizer follows HI/LO address construction, scaled
indices and call delay slots; previous-call clobbers invalidate tracked values.
Wrong offsets, strides, extra uses and reused witnesses decline. Local index
correspondence remains a hypothesis, not a semantic proof or inferred array size.

Frozen zero-model replay `v4-call-address-ground-replay-v2.json` clears the probe
table declaration and preserves all five call-address expressions. Best attempt
1476, source `c23d387481a25b5e8d499956f2690fc75a248a2a6e791e264cf598693c8e203a`.
Inventory v56 remains 24/25 frontend-cleared: ground alignment still fails on raw
stack addresses and missing stack aliases. Matrix and indexed vector/word storage
need reconstruction; merely adding undeclared scalar locals would not restore
shared storage. Existing `stack_object_repair` requires measured aggregate casts
and declared assignment-only aliases, so it does not yet cover this source shape.

## Verified o32 wide runtime interfaces and argument pairing

`wide_runtime_interfaces` recognizes complete target helper instruction streams
for low-64-bit multiplication and signed64 division, including argument packing,
return splitting and division traps. Runtime names only select probes; altered
operations, word order, returns or branch targets decline. Assembly metadata is
excluded from matching. `m2c_input.draft` supplies the verified two-wide-argument
interfaces and preserves their declarations in emitted candidates. The manual
prototype interface remains restricted to its existing primitive-word types.

m2c still renders annotated high/low words separately. `pair_arguments` combines
only the expected annotated chunks into two wide values, rejecting side effects
and unannotated arguments. Verified-helper result locals used in high-word slots
are projected to their upper32bits. This is source reconstruction, not runtime
execution admission or proof that the entire function is semantically correct.

Groundalignment replay `v4-wide-interface-ground-replay-v3` recognized2interfaces
and paired33calls. Helper declaration/arity errors clear; probe-table and missing
stack-object errors remain. Inventoryv55 stays24/25frontendcleared. Focused41pass;
final full1801passed22skipped57.19seconds.

## Measured record destination for a raw word copy

`record_word_copy.recover` handles a byte-member assignment wrapped in
M2C_UNALIGNED32 only when measured headers identify the containing four-byte
record, offset-zero one-byte member, array stride and signed-halfword index.
A unique target LW/SLL/ADDU/SWL/SWR sequence must match the named source,
destination, index and offsets. The candidate captures four source bytes once
and writes all four destination bytes, not just the first member. No source
aggregate type is invented; the actual source global lacks a public declaration.

`type_constraints.measure(layout_globals=...)` selects record/array roots from
canonical global types and includes their record-array element layouts. Repeated
compatible externs are accepted; missing/conflicting types decline. The target
compiler still measures all sizes and offsets, with hash-bound receipts.

Frozen results-flow replay `v3-record-copy-results-replay-v3` passes compiler and
frontend(best1435). Inventoryv54 is24/25cleared, only groundalignment remains.
NOT exact:4308positional bytes differ, weightedscore28.632. Differential64cases:
48pass16fail; first failure is candidate-only unmapped memory in indexed save
data access. Targetcoverage246/932instructions,55/152branch edges, not universal
semantic correctness. 41focused tests and1797fulltests pass(22skipped).

## Named record-word reads

`m2c_byte_view.named_record_word_reads` replaces the malformed aggregate cast
inside `M2C_UNALIGNED32((s32) M2C_FIELD(&global, Record *, offset))` with four
big-endian byte reads only when target dataflow resolves a matching named LW.
Wrong address, width, offset, clobber or local shadow declines. Bare value
wrappers are deliberately untouched because their destination width may also
need repair. The assembly hash and exact load sites remain in the receipt.

Results-flow replay `v3-record-word-results-replay-v1` reduces frontend errors
5->1; best1378 is still noncompiling. The remaining bestLapRecords[index].minutes
assignment must recover a four-byte record copy, not truncate to one byte.
Inventoryv53 stays23/25cleared. Focused33passed; full1793passed22skipped.

## Closed unaligned-copy values and byte cursors

The unaligned-store lowering also accepts bare stores through uniquely declared
unknown local cursors. `copied_word_temporaries` types a closed temporary as u32
only when its sole assignment is a witnessed four-byte read and its remaining
uses are unaligned-value wrappers. `closed_copy_cursors` requires a local void*
seed, explicit byte-view uses, literal steps witnessed in target instructions,
and no other uses; it proposes u8* and preserves byte arithmetic. Source/register
correspondence remains hypothetical; compiler/runtime gates remain authoritative.

Results-flow replay `v3-copy-cursors-results-replay-v1` recovers14cursors and2word
temporaries in the header branch. Frontend errors fall from the20-error cap to5:
timing-record raw-word copies remain. Inventoryv52 stays23/25frontendcleared.
The record's public header declares s8 minutes,s8 seconds,s16 fraction. Casting
the aggregate to s32 or writing just minutes is not a correct four-byte copy;
the next repair must preserve the entire record and target store width.
53focused tests and1790fulltests pass(22skipped). No new semantic claim.

## Bounded header alternative after incomplete lowering

`compile_recovery.byteview_redrafts` retains the assembly-first candidate and
tries one header-first alternative when unresolved M2C syntax remains and the
first draft did not already use headers. Both paths share public-ABI locking,
callback, global, address and local-declaration recovery. An alternative failure
does not discard the first candidate. Comments do not trigger another draft.

Frozen results-flow replay `v3-header-alternative-results-replay-v1` verifies
actual compilation attempts1314/1315(header) and1316/1317(assembly), plus their
normalizations. None compile; best1330 retains the previous source hash. Thus
header access is no longer missing from this path, but does not solve the
remaining cursor/unaligned-value reconstruction. Inventoryv51 stays23/25cleared.
28focused tests and1787fulltests pass(22skipped). No semantic success claimed.

## Unaligned unknown-field stores

`m2c_byte_view.unaligned32_field_stores` lowers standalone unknown field stores
wrapped in `M2C_UNALIGNED32` using target SWL/SWR width/offset witnesses. It
rejects intervening calls, memory operations and clobbers of either address or
value. A local u32 captures the RHS once before four big-endian byte writes,
preserving overlap without assuming aligned word storage. Source-to-register
address correspondence remains a hypothesis, not proven alias/layout knowledge.

Results-flow replay `v3-unaligned-store-results-replay-v1` now reaches candidate
generation with eight such stores instead of declining at an unknown field.
It still fails frontend on unknown local cursors; inventoryv50 stays23/25cleared.
The successful assembly-only lowering can retain unknown declarations and skip
header retry: candidate selection/completeness is a newly exposed engineering
gap, not a new frontend success. 50focused and1783fulltests pass(22skipped).
`inspect_header_redraft` preserves hash-bound assembly/header diagnostic drafts
without reading reference C bodies, for examining these remaining unknowns.

## Local call declaration holes without argument-slot loss

`local_call_interface.propose` runs on existing and fresh byteview candidates.
It preserves known parameter types, callback declarators and every argument
position. Unknown scalar slots require matching plain signed-word constants in
every paired source/target call. An unknown return becomes a pointer candidate
only when a bounded callee disassembly returns a call result also used as a
memory address. Existing headers take precedence; calls/body are never edited.
Receipts bind source, caller and callee hashes. This is not an admitted ABI.

This avoids trusting m2c's incomplete helper signature: the scheduler probe
reported only arg1/arg3, losing pass-through arg0/arg2. Frozen spiral replay
`v3-local-interface-spiral-replay-v1` now passes compiler/frontend (best1290).
Weighted score86.119,763 positional bytes different: NOT exact. All64 semantic
cases report failure; first divergence is an opaque matrix helper's stack
pointer. Target235/235instructions,13/14edges covered, but missing stack-object
effects/mapping prevent treating this as an isolated proven logic defect.
Inventoryv49:23/25frontendcleared,2remaining. Batchv5 replays both remaining
functions without crashes or unblocks; auditv5 accounts for2/2, not fixes.
Focused34passed; final full1780passed22skipped108.34seconds.

## Linker-map identity fallback for address-only globals

`unknowns.address_symbol_evidence` joins requested names against address-only
catalog definitions and exact standalone symbol rows in `build/*.map`. It
rejects conflicting definitions, never derives addresses from name suffixes,
and records matched definitions plus input file hashes. Both compile recovery
and model-repair address-only views consume this evidence. This is candidate
linker identity, not proof that the build matches the ROM or a type/extent claim.
The actual build map contains `D_801121E0 = 0x801121E0`, absent from the catalog.
Focused regression suite: 39 passed. Frozen spiral replay
`v3-linker-identity-spiral-replay-v1` confirms the global declaration is repaired;
four scheduler-declaration errors remain (previously five total). Inventoryv48
still has three unresolved frontend functions. Full regression suite pending.

## Inline indexed global byte-address stores

The existing address-only global repair now accepts inline primitive stores
with matching target width/offset and a last base-register definition adding
the live named global address. Independent symbol identity remains required.
Spiral-exit replay still declines: D_801121E0 is absent from symbol_addrs.txt's
map, despite matching target address/store evidence. Declines now expose which
uses remain unresolved. Next obtain build/link identity; do not trust a D_ name
suffix as evidence. Inventoryv47 remains three frontend blockers.

## Exact public record-tag spelling

`project_header_tag_parameters` restores a public struct/union tag spelling
only when an included typedef definition identifies that exact tag and alias.
`header_tag_definitions` performs exact lookup independent of model-facing type
packet size. ABI equality remains unchanged. Spiral-exit replay now reaches
byteview compilation, reducing10frontenderrors to5 declaration errors.
Baseline batchv4 completed all3remaining functions without crashes or unblocks;
inventoryv46 remains22/25cleared. Scheduler local ABI and indexed global remain.

## Measured callback source-call reconstruction

`callback_repair.propose` uses measured ABI consensus and binary call inputs to
render full callback calls, rather than patching arity alone. Maps parameters,
stack-address locals, explicit pointer seeds and incoming stack words; a checked
m2c FPR temporary-name bridge remains an explicit hypothesis for computed values.
Rejects side-effecting old arguments and unresolved mappings; whole-family edits
are atomic. Resampler replay now compiler/frontend passes, weighted75.5 and
189positionalbytesdifferent. Its64selectedsemanticpasses cover14/128instructions
only; callback paths are not validated. Inventoryv44:22/25frontendblockerscleared.

## Automatic callback parameter-layout candidates

Typed-callback redrafts now trigger target-compiler header measurement and
`callback_abi.parameter_candidates`. Each parameter-root family is tested against
the measured records; all family calls must resolve, and conflicting canonical
signatures remain ambiguous. The resampler automatically yields ALFilter and
ALResampler with ABI consensus. Neither root is declared proven and no runtime
contract is admitted. Actual callback source rewriting is still outstanding.

## Parameter-root callback layout hypotheses

`callback_abi.bind(..., parameter_objects=...)` can explore an explicitly supplied
parameter-record hypothesis through compiler-measured pointer fields. These
rows are labeled `hypothesis`, excluded from runtime ABI contracts, and do not
change default global-root binding. A target-compiler SDK probe confirms that
the ALFilter hypothesis matches both resampler callback paths and the five-word
ALCmdHandler ABI. Root selection and source-call remapping are not automated yet;
this is measured evidence for a candidate, not a frontend or semantic solve.

## Post-delay-slot outgoing argument evidence

Dataflow CallSite records four potential outgoing o32 stack words in addition
to a0-a3, after executing the delay slot. Unknown stack pointers/values stay
unresolved; captured words do not establish arity. Callback binding reports and
typed-callback redraft receipts now expose these values. Resampler replay
confirms sampleOffset in a3 and the incoming fifth argument forwarded on stack
at both calls, with the second computed a2 correctly unresolved. No call edits
or frontend acceptance result from evidence capture alone.

## Coprocessor-to-GPR provenance invalidation

Dataflow now invalidates destination GPR facts for mfc/dmfc/cfc operations.
It does not model coprocessor values; retaining an old parameter identity after
such a write was unsound. Resampler target audit verifies a2 is param2 at the
first callback and unknown after mfc1 at the second, while a3 retains param3.
Header ALCmdHandler has five arguments, with sampleOffset fourth and command
pointer fifth. Replay remains frontend-blocked pending callback signature and
argument remapping. The provenance fix is not a function-level solve.

## Typed indirect-call field lowering

Byteview lowering can preserve known-type function-pointer field signatures
when a target word load at that offset reaches an indirect call without a
register clobber. Redrafting now supplies assembly for these typed callback
fields, not just unaligned reads. The inferred callback signature is still a
hypothesis. alResamplePull replayv2 emits a candidate with one remaining error:
four supplied arguments versus five inferred callback parameters. It is not
resolved by relaxing the compiler or inventing an argument. Inventoryv40 still
four frontend-blocked functions.

## Indexed scalar byte-unit correction

`address_units.indexed_scalar_reads` recognizes a scalar extern read whose
explicit source scale matches a target HI/sll/addu/relocated-word-load sequence.
It proposes a byte-address cast before the word load, removing accidental second
scaling from C pointer arithmetic. Runs on normalized candidates as well as
fresh byteview drafts; the normalized entry point was necessary in MotorStop.
Replayv2 improves weighted score49.16->50.615 and positional distance563->549.
Selected semantic coverage stays19/103instructions: not universal validation.

## Unknown extern used as a shifted call address

`m2c_byte_view.unknown_address_externs` proposes an extent-unspecified byte
array view for an unknown extern with one address use in a shifted field-based
second call argument. Requires matching target HI/LO, shift, address addition
to a1 and callee. Extra symbol uses decline. It does not prove object extent or
source-field/register identity. Actual MotorStop replay now compiler/frontend
passes (weighted49.16,563positionalbytesdifferent). Thirteen selected semantic
passes cover only19/103 target instructions and1/14 branch edges; unsupported
execution, memory faults and step limits remain. Double-scaled initialization
table indexing is still suspected and not discharged by early-return passes.
Inventoryv37:21/25 frontend blockers cleared, four remain.

## Schedule-independent feeding-pointer witness

`stack_byte_steps` replaces the literal feeding-cursor opcode sequence with a
bounded local def/use check. Tracks the original stack-loaded value, kills
clobbered register facts, requires one store of original+1 to the same slot,
and checks the backedge/delay slot. Calls, stack-pointer changes, internal
entries and unknown/overlapping stores decline. Both SDK schedules replay:
MotorStop now has only an unknown global declaration error; ContRamRead retains
its prior source hash. Inventoryv36 remains five frontend-blocked functions.

## Remaining-frontend replay after packet-copy repairs

`frontend-remaining-replay-batch-v3.json` completed all five exposed-function
replays with zero model calls and no child failures. None cleared the frontend;
inventoryv35 remains20/25cleared. Engineering auditv3 binds all five residuals
to sources and owners; classification is not a fix or fresh-function validation.
MotorStop shows partial transfer of storage, aliases and copy cursors, but its
feeding-pointer schedule differs from the literal opcode matcher. Next replace
schedule dependence with register-def/use evidence. Unknown MotorStopData
byte-address declaration and likely double-scaled Motorinitialized indexing
remain separately tracked. No claim that these functions are semantically right.

## Bounded indexed stack copy-out

`m2c_copy.indexed_stack_read` checks a zero-initialized byte-copy target loop,
matching register flow, stack index, increment, comparison and backedge, plus
the corresponding source do-while. A raw stack read becomes a reconstructed
buffer access only when the full indexed range fits its witnessed storage.
Source/target correspondence remains a hypothesis, not semantic proof.
`v3-copy-index-packet-replay-v1` clears __osContRamRead compiler/frontend gates:
weighted score50.611,657positionalbytesdifferent. Semantic validation unavailable:
no completed targetcase, unsupported lwl execution and step limits. SDK opaque
callees/unknown helper ABI remain debt. Inventoryv34:20/25cleared5remaining.

## Stack-resident feeding byte cursor

`m2c_copy.feeding_cursor` reconstructs an anonymous-union stack cursor that
only takes one named object's address (possibly repeatedly), increments by one,
and feeds a recognized copy cursor. Requires the target's same-slot stack
load/increment/store with backedge; protects intervening loaded-register writes.
Retypes only the local and casts its seeds; headers remain unchanged. This is
a source/stack-name correspondence hypothesis. Replay
`v3-copy-feed-packet-replay-v1` leaves one SDK frontend error: raw indexed sp.
Inventoryv33 still six blocked functions; no semantic acceptance claimed.

## Copy source cursor and endpoint

`m2c_copy.source_cursor` uses the single target copy-loop witness to propose
byte pointers for a closed anonymous-union cursor and its endpoint. Requires
one seed, one12-byte step, four explicit unaligned reads at0/-8/-4/0, one
comparison and endpoint fieldindex*4 matching target loop span. Extra uses
decline. Does not infer the anonymous union's type or layout. Compiler and
semantic gates remain authoritative. Replay `v3-copy-source-packet-replay-v1`
reduces SDK frontend diagnostics from6errors1warning to4errors0warnings;
root cursor and indexed stack access remain. Inventoryv32 still six blockers.

## Copy destination storage reconstruction

Closed unknown destination cursors can now become u8 pointers when all uses
are one reconstructed-root seed, one12-byte step and exactly four s32 field
stores at -12/-8/-4/0. Escapes, other uses, offsets or strides decline cursor
retyping. SDK replay `v3-copy-cursor-packet-replay-v1` removes destination
cursor errors, leaving6 frontend errors in source views/endpoint/raw stack.
Inventoryv31 remains six blocked functions; no new semantic acceptance.

`m2c_copy.destination_storage` connects a single witnessed aligned copy to an
unknown `spHEX` local, proposes aligned u32 backing storage and byte-address
uses, and maps undeclared byte aliases only with matching target stack lbu
offsets. Declared neighboring objects, parameters, missing load witnesses and
direct alias mutations decline. This remains a stack-name correspondence
hypothesis, not proof of object extent or semantic equivalence.
Actual `v3-copy-storage-packet-replay-v1` emits this reconstruction; best1076
still fails compilation on unknown cursors and indexed stack access.
Inventoryv30:19/25 frontend blockers cleared, six remaining.

## Unaligned copy-loop extent evidence

`m2c_copy.unaligned_loop_layout` identifies a closed12-byte unrolled lwl/lwr
copy loop with aligned word stores and a4-byte tail. Validates registers,
offsets, increments and backedge; reports target instruction range, stack offset
and copy extent in redraft receipts. SDK replay confirms40bytes at sp0x2C.
It does not yet allocate C storage or resolve aliases; no C object-bound proof.
Inventoryv29 remains six frontend blockers.

## Explicit unaligned32 read lowering

`m2c_byte_view` can lower explicit unaligned32 wrappers over plain pointer names
or unknown scalar fields into four byte reads assembled big-endian. Requires
matching adjacent target lwl/lwr pairs with base/register agreement and offset+3.
Reports source/assembly hashes and witness indices. Source-to-register identity
is still a hypothesis; no alignment, allocation or whole-function correctness
claim. SDK replay v3-unaligned-packet-replay-v1 now emits a lowered candidate,
but remaining unknown stack objects/cursors prevent compilation. Inventoryv28
still six unresolved frontend functions. Corrected full suite1730passed22skipped.

## Exact public integer parameter projection

Byte-view redraft now has an o32-only `s32` generated parameter to public `int`
projection: rename the public parameter and initialize an s32 working local with
the original name. Other parameter types and return types are not relaxed;
signature equality is checked normally. Candidate receipts record projection
hashes. SDK replay v3-public-word-packet-replay-v1 passes the signature gate but
declines unknown-width M2C_UNK scalar copy fields. No new frontend unblock.

## SDK redraft signature obstruction

Source-bound audit `sdk-packet-redraft-audit-v1.json` reproduces __osContRamRead
redraft rejection: header `int` parameter becomes m2c `s32`. The signature gate
remains exact; rejection now prints both shapes. Existing fixed-word prototype
seeding cannot accept this pointer-bearing public signature. This is an unresolved
context-projection gap, not evidence the unaligned-copy lowering itself failed.
Target copy is40bytes using lwl/lwr into aligned word stores; preserve alignment
when evaluating a future redraft. No new frontend or semantic success claimed.

## Incomplete local tag versus measured typedef

`aggregate_scalar_repair` can replace `struct T local;` with `T local;` for a
fresh incomplete-tag diagnostic when a source-bound target-compiler layout
already exists for typedef T. No synthesized record body or global declaration.
`modelrepair` routes this diagnostic into measurement. SDK packet replay
v3-typedef-packet-read-replay-v1 removes this error but remains noncompiling on
unaligned-copy representations and aliases. Inventoryv26 still six unresolved.

## Unknown-type header recovery and typedef aliases

`modelrepair` invokes existing header recovery on unknown-type diagnostics.
`compile_recovery.header_variant` recognizes simple typedef aliases/forward
typedefs in addition to inline record definitions. These imports provide names,
not missing layouts; compiler and existing draft recovery still adjudicate.
Nameplate replay v3-type-header-nameplate-replay-v2 passes compiler/frontend and
64 sampled semantic cases (26/26 instructions,2/2 branch edges), with opaque
callee and finite-input debt. Still62bytes different. Inventoryv25:19of25
frontend blockers cleared,6remaining. No fresh-transfer proof implied.

## Symbol-bound indexed absolute load widths

`indexed_address_repair.absolute_load_witnesses` matches a closed shift/subtract/
shift table-index sequence with unsigned-halfword parameter-load provenance,
matching HI/LO symbol and independent absolute-symbol map. The frontend repair
uses the observed scalar load opcode to insert the missing pointer type.
Nonmatching strides, widths, parameters, blocks or register aliases decline.
Collision replay v3-indexed-load-collision-replay-v1 passes compiler/frontend;
semantic panel has6passes58failures, so correctness remains unresolved.
Inventoryv24:18of25 original frontend blockers cleared,7remaining.

## Target-bound typed parameter byte offsets at calls

`frontend_repair` matches a fresh incompatible-pointer argument diagnostic to
an ordinary pointer parameter plus literal and the exact target paramN+offset
value at the corresponding callee/argument word. Emits byte-base arithmetic,
not a cast of struct-scaled arithmetic. Declines mutations, shadows, nonunique
witnesses and unsupported multiline shapes. Collision replay
v3-param-offset-collision-replay-v1 removes two errors; indexed absolute load
still fails. Inventoryv23 remains eight unresolved functions.

## Current remaining-queue replay (batch v2)

All eight remaining functions were replayed from frozen checkpoints after the
HUD/character-select changes. Batch `frontend-remaining-replay-batch-v2.json`
completed with zero model calls and no child crashes. All eight retained exactly
their previous best source hashes and remain noncompiling/frontend-failed.
`frontend-engineering-audit-v2.json` binds diagnostics, source/receipt hashes and
owners for all eight; none unclassified. This is exposed-function regression
evidence, not fresh generalization or solved coverage. Inventoryv22:17 of25
original frontend blockers cleared,8 unresolved.

## Affine table address stores

`indexed_address_repair.affine_table_stores` follows block-local shift/add/subtract
arithmetic from named unsigned-byte index and table-address seeds into a named
global pointer store. This supports non-power-of-two byte strides. Diagnostic-
bound frontend repair casts the table base to a byte pointer before addition;
no table extent or semantic certificate. Character-select replay
v3-affine-table-course-replay-v1 now passes compiler/frontend. Semantic testing
is unavailable due to synthetic global-stride bounds. Inventoryv21:17 of25
original blockers cleared,8 remain. Fresh transfer validation still required.

## Header zero-arity calls with retained expression evaluation

`frontend_repair` projects a fresh expected-zero-arguments diagnostic onto a
unique included `void callee(void)` declaration with one matching target call.
Up to four simple draft expressions are retained as void operands of a comma
expression before the zero-argument call. No side-effect deletion or prototype
change; still header-assisted, not proof from callee code. Character-select
v3-zero-arity-course-replay-v1 now compiles under IDO, but one frontend table
pointer/stride error remains. Inventoryv20 still nine unresolved functions.

## Target-o32 single-pointer callback views

`frontend_repair` can cast a compiler-diagnosed `void(Actor *)` callback to
`void(void *)` when the o32 target passes that exact callback symbol as first
argument to the same callee at one identified call. Fresh excerpt and bounded
signature required. This is a target ABI candidate, not portable C compatibility
or runtime proof; headers remain unchanged. Character-select replay
v3-callback-view-course-replay-v1 clears both callback errors, leaving table
stride and zero-argument call mismatch. Inventoryv19 still nine unresolved.

## Zero-only aggregate member writes

`aggregate_scalar_repair` permits a single zero-only global use to select a
measured unsigned byte/halfword member when the target has one matching-width
zero-register store at that symbol base. Records `signedness_inferred=false`:
zero bits match without proving original signedness/member spelling. Reads,
nonzero values and multiple uses decline. Actual character-select replay
v3-union-zero-course-replay-v1 removes the union-store error; four frontend
errors remain. Inventoryv18 still nine unresolved functions.

## Target-witnessed array first-element zero store

`frontend_repair` changes plain `array = 0` to `array[0] = 0` only with a fresh
primitive-array diagnostic and unique matching-width target zero-register store
at the exact global base. Requires o32, no shadow or multiple source assignments.
Does not clear the full array. Character-select replay v3-array-zero-course-
replay-v1 removes two errors; five remain and the function is still noncompiling.
Inventoryv17 stays nine unresolved functions; no semantic acceptance implied.

## Target-load-selected global aggregate member

`aggregate_scalar_repair` combines current target-compiler layouts, a unique
global declaration and diagnostic-bound source with homogeneous target signed/
unsigned byte/halfword loads. Matching-width stores must also address offset0.
Chooses the unique matching scalar member across bare source uses; declines
mixed loads, offsets, address escapes and shadows. Records assembly hash and
access indices; compile/runtime adjudication still required. Character-select
replay v3-global-load-course-replay-v1 removes nine course-index errors through
`signedValue`; seven frontend errors remain. Store-only unions are not assigned
signedness without evidence. Inventoryv16 still nine unresolved functions.

## Referenced-global type packet priority

`compile_obligations.header_types` prioritizes types from referenced included
extern declarations, then connected function-family suggestions, then other
explicit source types. This fixes omission of globals' types without letting a
large callback command union consume the family packet. Target-compiler probes
still establish actual offsets and widths; header selection is not binary type
proof. Character-select now exposes measured signed/unsigned union alternatives;
choosing an arm still requires target access/role evidence. No new unblock.

## One-dimensional array address decay

`frontend_repair` handles fresh compiler-diagnosed `T (*)[N]` to `T *`
assignment in plain `p = &array;` by removing the address operator. Element
spellings must match; multidimensional arrays, arithmetic and stale excerpts
decline. No change to declarations, sizes or compiler policy. Character-select
replay v3-array-address-course-replay-v1 removes two such errors but still has
16 frontend errors. Inventoryv13 remains nine unresolved functions.

## Header-backed unknown byte cursors

Existing `m2c_byte_view.unknown_local_cursors` now accepts an optional unique
included-header object declaration as address-seed context, in addition to plain
primitive externs in the draft. Allows explicit integer-address comparisons;
still requires closed literal steps and explicit byte fields, rejects escapes
and shadowed parameters/seeds. Records the declaration; it is not measured
layout or allocation evidence. Actual character-select replay v3-header-cursor-
course-replay-v1 removes unknown-local errors but still fails frontend checks.
Inventoryv12 remains nine unresolved functions; no semantic or transfer claim.

## Header macro versus draft extern collisions

`frontend_repair.propose` removes plain draft extern declarations that collide
with compiler-observed header field macros. Requires exact diagnostic excerpt,
macro expansion note, current include-confined header definition `(object.field)`
and backing extern object declaration. Records header hashes; no header changes.
Character-select replay v3-macro-extern-course-replay-v1 removes these errors but
remains noncompiling due to unknown local/aggregate/callback types. This is a
verified intermediate repair, not another fully unblocked function or transfer
validation. Inventoryv11 still has nine remaining frontend functions.

## Decimal formatting buffer reconstruction

`solver/format_buffer_repair.py` proposes a bounded local array from a scalar
formatting root and stack endpoint aliases. It requires simple decimal format
bounds, matching target sprintf destinations, available frame span and no
overlapping wide accesses. Undeclared byte-read aliases require matching target
unsigned byte loads; endpoint addresses must be observed. The o32-gated
normalization candidate is compiler-checked normally and records source/assembly
hashes. Allocation and source-to-stack correspondence remain hypotheses.
HUD replay v2 best755 passes compiler/frontend with zero model calls; semantic
validation is unavailable, not a pass. Inventoryv10 leaves nine frontend blockers.

## Remaining-frontend batch replay and engineering audit

`replay_frontend_remaining.py` reruns an immutable inventory's unresolved functions
through the real zero-model entrypoint, verifies checkpoint hashes, refuses reused
outputs, records incremental progress and stops on child failure. Batchv1 finished
all10remaining functions: zero compiler/frontend unblocks, no process crashes.
These are exposed-function replays, not fresh generalization tests.

`audit_frontend_batch.py` binds current child receipt/source hashes and full
diagnostics to manually selected module owners and next actions. Unknown functions
remain explicitly unclassified; changed sources and unfinished batches reject.
`frontend-engineering-audit-v1.json` accounts for all10current failures as unresolved,
not repaired: ABI/declaration recovery, stack/packet objects, pointer/address units,
and unknown local/global types. Five functions produced no normalization candidates.
Inventoryv9 remains10unresolved. Prioritize buffer/endpoint reconstruction and
coordinated private ABI recovery, then repeat fresh-batch validation before completion.

## Literal-word unknown prototypes and zero-register constant flow

`literal_call_repair` creates candidate-local prototypes for unknown declarations
used only by standalone discarded-result calls with matching positive32bit literal
argument registers in the target. No outside-function callee uses or guessed
variable arguments. Uses whole-function dataflow or a bounded block/single-
predecessor fallback; the latter supplies constants, not a reachability proof.
The generated void/s32 declaration is a call-site projection, not the original
callee signature. Normalizer gates it to big-endian o32.

Dataflow now recognizes addiu/addi from the architectural zero register as a
constant assignment. Existing wide-word argument packing ignores noncode comments
when validating expressions, retaining its side-effect and header-signature checks.
Real replay `v3-literal-prototype-hit-replay-v4.json` unblocks hit-reaction compiler
and frontend with zero model calls.28semanticpasses36inconclusive (__ll_lshift
unknownarity),129/655instructions,1904positional bytes differ. No exactness claim.
Inventoryv9:15of25original frontend blockers unblocked,10remaining.
Full regression suite1697passed22skipped; baseline unchanged, allworkers terminal.

## Pointer-valued call loads and byte-array address units

`frontend_repair` proposes pointer-word views for diagnostic integer-to-void-pointer
arguments when target dataflow supplies the same first-parameter-offset word load
to the same unique call/argument. `byte_array_decay` removes whole-array pointer
scaling for closed indexed uses of a declared local byte array, bound by diagnostic
text and target stack-base addition evidence. Source index identity remains a
hypothesis; these do not establish C object extents or semantic correctness.

Normalization cap increased from4 to8: _Litob's valid next candidate was reachable
only after the old cap. Duplicate suppression, beam bound and no-child early exit
remain. Actual `v3-litob-address-views-replay-v2.json`:25normalizations,zero model
calls,compiler/frontendpass. All64semantic cases inconclusive due candidate-only
__ll_lshift helper with unknown call arity;75/166targetinstructions covered.
Other SDK callee contracts remain unresolved.419positional bytes differ.
Inventoryv7:14of25original compiler/frontend blockers unblocked,11remaining.
Normalization regression now checks a six-stage chain, the eight-stage hard cap,
parent lineage and duplicate cycles. The old four-stage expectation failed after
the intentional cap change and was updated; no acceptance gate was relaxed.
Final full suite1693passed22skipped; all workers terminal, baseline unchanged.

## Coordinated output buffer and two-wide-operand o32 candidate

`stack_result_repair` handles one closed dialect: a scalar stack root passed to
a private three-argument declaration, two missing result aliases, and target
evidence for four first-pointer output words plus two64bit operands. It traces
direct caller stack loads into a2/a3 and outgoing stack words16/20 (including the
call delay slot), requires callee incoming stack reads and no use of initial a1,
and rebuilds storage and argument packing together. No function-name allowlist.
Other-function callee uses, ambiguous slots and incomplete evidence decline.
Only candidate-local prototype/storage edits; compiler and semantic gates remain.

`v3-stack-abi-litob-replay-v2.json`: zero-model _Litob replay removes both missing
stack aliases with sp50[4] and both packed operands. Two frontend errors remain;
no semantic validation or proof of original C aggregate-return type. Inventory
unchanged at12 unresolved. v1 retains the delay-slot evidence gap found and fixed.
IDO compilation passed(score32.695), frontend still rejected. Full1690passed22skipped;
final cross-function scope guard focused2passed. No semantic promotion.

## Stack-result model experiment and disconnected-alias guard

Actual GPT-OSS20b experiment `v3-litob-stack-evidence-model-v1.json`: two calls,
3134 tokens, no compiling children. Both recorded prompts contain the new evidence.
The second proposal declared independent uninitialized stack aliases and introduced
pointer-plus-pointer arithmetic. Its structural validity was not compilation or
semantic success. This test shows the evidence feed alone did not solve this case;
it is not an ablation or broad assessment of model capability.

`stack_result_evidence.validate_candidate` now rejects newly introduced bare scalar
aliases that receive neither an assignment nor an address-passed output when
source-bound caller/callee evidence associates them with another result buffer.
Runs after edit application and before compiler evaluation, returning a concrete
repair error. Actual saved model candidate is rejected by the guard. This does not
accept arbitrary initialized aliases, establish ABI correctness or reconstruct
storage; remaining candidates still face compiler and differential gates.
Full regression suite:1688passed22skipped; baseline unchanged.

## Missing stack-result aliases: caller/callee diagnostic feed

`stack_result_evidence` correlates undeclared `spHEX` aliases with a unique
address-of-stack call in candidate and target, target post-call loads, and callee
stores through its first pointer parameter. Reports word offsets, instructions,
caller argument observations and nearby outgoing stack stores, with source and
assembly hashes. It does not certify a C object extent, all-path writes or ABI.
Only extracted assembly is read. Ambiguous/missing evidence declines; unavailable
files or unsupported parsing are logged without stopping model repair.

The report is appended to modelrepair prompts. `stack_result_report.py` reproduces
it from a completed source-hash-bound replay. `_Litob` report
`v3-litob-stack-result-v1.json` maps sp54/sp5C to +4/+12 of sp50, backed by lldiv's
four word stores. Caller also supplies a2/a3 plus stack arguments, omitted by the
draft's three-argument prototype/call. Required repair is coordinated output-buffer
storage and complete argument ABI—not uninitialized local declarations. Prompt
extraction is verified; automatic reconstruction and model benefit are unverified.
Full regression suite1687passed22skipped; baseline unchanged.

## Root-bound parameter fields and bounded byte-array pseudo-fields

`void_field_repair` now requires dataflow `param0` and exact root-offset binding
for first-pointer-parameter accesses. Unrelated same-displacement accesses no
longer make these fields ambiguous; later parameters decline until ABI word
mapping is justified. Local cursor fields remain explicitly coarse hypotheses.
`frontend_repair` lowers diagnostic-bound byte-array `.unkNN` fields only for
an explicitly declared local byte array and in-bounds index. No extent inferred.

Transfer replay `v3-array-field-litob-replay-v2.json`: all void and array
pseudo-field errors removed from _Litob; four errors remain (two missing stack
values, pointer-to-array stepping, scalar-loaded destination pointer). Zero model
calls, six normalizations, not compiled, no semantic result. Inventory unchanged:
12frontend blockers. Next needs callee-bound result-object and address-unit evidence.
Full regression suite:1685passed22skipped; baseline database unchanged.

## Void-pointer pseudo-field byte views

`void_field_repair` proposes typed byte accesses for `void_pointer->unkNN` based
on fresh diagnostic locations and target nonstack load/store offsets. Requires
unique void-pointer local/parameter, unambiguous width and integer/float class,
matching access role, and unambiguous load signedness for reads. It rejects stale
diagnostics, shadowed/nonvoid bases, stack-only evidence and ambiguous widths.
This is coarse offset correspondence, not proof of matching base objects. No
shared struct layouts or extents are created; normal compiler/semantic gates apply.

`v3-void-fields-preview-replay-v1.json` unblocks initCourseSelectPreviewModelIn
compiler/frontend with zero model calls. All64 semantic cases fail, although
70/70target instructions execute. First difference is opaque transformVec3iByFixedMatrix
receiving different stack buffer addresses, already classified as unmodeled
opaque-stack-pointee debt; persistent memory also differs. Keep failures until
validated callee execution/effects establish whether the candidate is wrong.
Inventory v6:13 of25original frontend blockers unblocked,12remaining.
Regression suite1683passed22skipped; final focused guard/inventory6passed.

## Measured scalar reads from aggregate locals

`aggregate_scalar_repair` now handles diagnostic-bound `(s32) local`-style reads
when a uniquely declared aggregate local has one compiler-measured zero-offset
scalar field of matching spelling and width. No field is inferred from its name;
parameters, shadows, arrays and pointer fields decline. All aggregate proposals
now require a measurement bound to the current source SHA and measurement kind.
The normalizer also handles literal byte-offset assignments from void-pointer
parameters to local pointers, retaining the void-pointer result.

Actual zero-model replays: `v3-first-scalar-preview-replay-v2.json` removes the
preview vector cast and initial pointer arithmetic, leaving 12 void pseudo-fields.
`v3-first-scalar-collision-replay-v1.json` transfers the scalar-read repair to
the collision function, leaving two typed byte-offset call arguments and an
integer-address dereference. Neither compiles yet; this is mechanism transfer on
exposed functions, not fresh-function validation or a new frontend unblock.
Full regression suite: 1680 passed, 22 skipped; baseline database unchanged.

## Measured pointer-table views and header-bound byte cursors

`indexed_address_repair` also handles an already byte-addressed scalar word load
assigned to a primitive pointer local. Requires a fresh compiler-measured global
index field and unique target load/stride witness; preserves the table declaration
and rejects parameter/global shadowing. `frontend_repair` casts closed byte-cursor
offset arguments to the included header's exact ordinary pointer parameter type.
Fresh diagnostic location, unique local byte pointer and unambiguous full header
signature are required. These propose pointer views, not layout/semantic proofs.

`v4-mesh-types-replay-v1.json`: initRaceIntroModelMeshes compiler/frontend pass,
0 model calls, 12 normalizations, 150 positional bytes differ. Only 12 selected
semantic cases pass with execution debt (35/82 instructions). Stress rejected
122 target trials with memory faults. Exploration examples show out-of-range
synthetic course indices and opaque allocator handles used by concrete heap lookup.
The latter needs validated producer effects/valid handle environments, not silently
accepting opaque-vs-concrete incompatibility. No new byte-exact claim.
Inventory v5: 12/25 original frontend blockers unblocked, 13 unresolved.
Full suite: 1678 passed, 22 skipped.

## Aligned-copy pseudo-operations and loaded-pointer offsets

`m2c_copy.propose` emits a bounded C89 word-copy loop for the installed decompiler's
`M2C_MEMCPY_ALIGNED(destination, source, byte_count)` pseudo-operation. Addresses
are evaluated once; sizes must be positive multiples of four, at most 4096 bytes.
Side-effecting arguments, custom declarations and local-name collisions decline.
This is a candidate, not proof of source addresses, alignment or copy semantics.
Modelrepair routes it on explicit frontend implicit-declaration diagnostics and
big-endian o32, even when IDO already compiled with an implicit-function warning.
The normalizer separately converts explicit loaded void-pointer byte arithmetic
to unsigned-byte-pointer arithmetic with a void-pointer result.

`v4-aligned-copy-replay-v3.json`: initRaceIntroModelMeshes now IDO compiles after
three zero-model normalization candidates, but two frontend failures remain:
integer-loaded pointer table and byte-cursor translation argument. No semantic
or exactness claim. Earlier v1/v2 receipts document the failed combinations and
the compiler-only routing gap. The unresolved inventory remains 14 functions.
Full regression suite: 1676 passed, 22 skipped. Baseline database hash unchanged.

## Pointer-table loads with byte strides

`frontend_repair` now handles a closed m2c integer-to-pointer assignment where
the table was declared as a scalar and the index already contains a byte stride.
It requires fresh diagnostic text, a local void-pointer destination, a first
pointer parameter index load, and a unique matching binary load width, signedness,
offset, shift, table relocation and word load. Only big-endian o32 is admitted.
It proposes a pointer-sized load through a byte-addressed table view; no public
type, table extent or acceptance policy changes. Target parameter-index witnesses
are opt-in in `indexed_address_repair`; existing global measurement guards remain.

Actual zero-model replay `v4-pointer-table-replay-v1.json` unblocked
`updateThrownPickupSpawner`: compiler/frontend pass, 64 sampled semantic passes
with execution debt (122/160 reachable instructions). 513 positional bytes still
differ. Regression suite: 1672 passed, 22 skipped. Source-bound inventory v4:
11 compilation unblocks out of 25 original blockers, 14 unresolved. Transfer of
this new repair to other functions remains unverified.

## Complete/incomplete pointer offsets and first array elements

frontend_repair now proposes byteoffset expressions for primitiveclosed local
pointerassignments from an incompletetype parameter. For completepointertypes,
it requires a targetdataflow witness for the firstparameter plus the same
literalbyteoffset; ordinary typedindexing is not blindly recast. ELF32o32gate,
freshdiagnostics,declaredpointerdestination and unchangedpublicsignature remain.

aggregate_scalar_repair now supports closedscalarread/modify/write on a local
aggregate whose uniqueoffsetzero field is a compiler-measured array of that
exactscalartype/width. Selects[0] for bothreadandwrite; excludes parameters,
shadowedlocals,ambiguousfields andnonmatchingtypes. These are candidatehypotheses,
not proof of recovered C layout or aggregate-copy behavior.

renderRacePickupIdle actualentryreplayv2:0modelcalls,compiler/frontendpass,
5sampledsemanticpasses59failures,1811positionalbytesdifferent. v1 showed that
completeheadercontext bypassedincompletepointerguard; v2addedtargetbytewitness.
Inventoryfrontend-inventory-v3.json:10compilerunblocks/25original,15unresolved.
Full1671passed22skipped,baselinehashunchanged. No new exact/semanticclaim.

## Stored symbol addresses and measured first-field assignments

Extended existingaddress_units.address_only_globals for incompletebyte-array
views ofunknownexterns usedonly as pointer-valuedstores. Requiresnamedtarget
HI/LO and dataflowstorevalue match; no guessedobjectextent/contents. Normalizer
nowinvokes thispath forunknownexterns too, withreceiptparentage.
aggregate_scalar_repair proposes uniquezero-offset scalarfield assignments
fromconfiguredcompiler-measured headerlayouts andfreshmatchingdiagnostics.
Ambiguousfields/unions/arrays/mismatchedtypes decline; not an aggregatecopyproof.

renderRacePickupRespawn entryreplay0calls passescompiler/frontend;17sampledpass,
47failed(firstknownresidual opaqueallocFixedTransformMatrix stackidentity).
renderRacePickupIdletransfer improvedbutstill2compilererrors(incompletepointer
arithmetic andTransform3D scalarcast). frontend-inventory-v2:25originalblockers,
9compilationunblocks,16remain. Full1668passed22skipped. No sourceintegration.
Firstaddressreplaycrashedimportscope; fixedandv2completed. Orphaninputreceipt
preserved; no successfulrunclaim forv1. Fullgoal remainsunfulfilled.

## Integer-address recovery and source-bound blocker inventory

frontend_repair now proposes casts for a closed unsigned32parameter ORliteral
address expression read into a declared primitiveoutputpointer. Requiresfresh
diagnosticexcerpt andELF32big-endiano32; no arbitraryscalar dereference changes.
__osSiRawReadIo entryreplayv2 passescompiler/frontend,64sampledsemanticswithdebt,
1positionalbyte residual—notexact. Full1661passed22skipped. No referencebodies,
integration,baselineorhistoricalcheckpoint changes.

frontend_inventory.py inventories all25originalfreshv3/v4compilerblockers with
checkpoint/sourcehashes and binds8compilationunblocks to savedreplayreceipts.
17remain in frontend-inventory-v1.json. Rawcampaign and semanticoutcomes retained
separately. Unknown effects/semanticfailures are not excused by compiler success.

## Narrowed frontend objective

See FRONTEND_GOAL.md. Goal API refused replacement ofunfinishedgoal; no false
completion performed. First fix `negative_field_repair` lowers negative m2c
pseudo-fields on primitive localpointer/arraypointer views using matching
nonstack targetoffset/width/accessclass. Preserves publictypes; shadowed and
unknown views decline. Hooked into resilient normalization with receipts.
guPerspectiveF zero-model entry-point replay nowcompiler/frontendpasses,
semanticunavailable (`v4-negative-perspective-replay-v1.json`,attempt412).
18inventoriedcompiler/frontendblockersremain. Full1658passed22skipped,
then4focusedtests verified additional shadowguard. No new exactclaim.

## Fresh-v4 final result and next goal phase

Worker51508 exited0. Both batches stalled after available strategy exhaustion,
not workbudget exhaustion:22+14workitems,0modelcalls,12freshfunctions.
2exact:alAuxBusNew/updateRacePlayerMode32Character3.2samplednonexactpasses:
func_800628DC/drawRaceSetupSavePanelIcons(the latter hasexecutiondebt).
5noncompile,1frontendfailure,1unavailable,1parked. Original checkpoint receipts
remain unchanged. No universal semantic or whole-game completion claim.

Next environment gap: makeFixedRotationYZX targets never completed in1150trials.
Opaque rotation producers do not populate matrix buffers, while concrete
multiplyFixedMatrix3s reads them and refuses uninitialized stack bytes.
Requires validated producer effects/execution, not more source repairs or
random seeds. corrupted_init instead has missing/ambiguous target identity
(0resolutions), not a proven hardware backend requirement. Other remaining
compiler families: negative-offset pseudo-fields, integer address dereference,
pointertable representations, unresolved aggregates/globals and widecalleeABI.
Broad goal work can continue; no current worker and goal remains unfinished.

## Local record-layout repair and resumed fresh validation

`solver/local_record_repair.py` now supplies resilient normalization with a
coordinated private-record layout hypothesis. It only handles pointer-used
candidate typedefs composed of padding and offset-named fields. Target nonstack
load offsets/widths filter the hypothesis; they do not prove common object roots.
Unknown/mixed widths, non-offset fields, by-value uses, accessed padding and
overlaps decline. Pointer widths stay four; scalar-width changes require one
observed load form. All proposals retain ordinary compile/semantic gates and
source/assembly hashes. No header or public signature changes.

`_allocatePVoice`: three localrecord fixes remove the observed layout failure,
including padding shrink, missing signedhalfword field and padding expansion.
Zero-model actualentry replay `v3-record-voice-replay-v2.json`:64sampledpasses
withdebt,score85.776,161positionalbytesdifferent,notexact. Separate explicit
valid-list controls `v3-record-voice-paths-v1.json`:7/7pass,57/57instructions,
12/12conditionaledges. Not automatic pointer-graph seed generation or universal
equivalence. No other current54candidate meets this narrow repair's preconditions;
transfer remains unproven. Suite1655passed22skipped,1022terminal.

Fresh-v4 running51508: frozen patchedcode,0modelcalls,40workitems perbatch,
12freshfunctions across6eligiblecallingstrata.614exposednames excluded from
195selectionreceipts. Keep originalcampaign and controlledreplays separate.

## 2026-09-07: completed fresh-v3 failure replay

Fresh-v3 is TERMINAL (both160-work-item budgets exhausted; worker8615 exit0),
not live. Full audit and unresolved families:
[fresh-v3-failure-followup](eval/experiments/campaign-gap-audit/fresh-v3-failure-followup.md).
New frontend_repair module is wired into resilient modelrepair normalization:
diagnostic-bound address conversions and header-guided o32 64-bit argument
packing, with compiler validation and parent receipts. Added plain void-pointer
call-offset lowering; fixed existing arrow-as-subtraction normalization bug.
Six of19 final compiler/frontend blockers pass both gates in zero-model probes
(`frontend-v3-replay-v2.json`), not six semantic/byte matches. Original cohort,
frozen code and baseline unchanged.

Actual entry-point loadRaceCourseAssets replay: four normalization rounds,
64 sampled passes with execution debt; _allocatePVoice now compiles but64fails,
revealing wrong candidate record padding. Unknown direct-call argument-only
disagreements now become inconclusive, with raw failures retained and panel
policy identity bound. Menu settings35pass/29inconclusive; unlock39pass/
19inconclusive/6stillfailed. No fabricated arities and no mismatches waived as
passes. `break` is a control-flow/trap backend limitation, not blanket proof of
hardware-only source. Thirteen compiler blockers and further runtime/environment
gaps remain. Final full suite1649passed22skipped (26029 terminal,25.99sec).

Fresh-v3 general-failure followup (outside active frozen snapshot): existing
repair_context normalization handled literal void-pointer increments but not
local base+index*stride expressions. Added void-local-byte-arithmetic candidate
generation for uniquely declared ordinary void* locals, IDO operand diagnostics,
no signature edits, <=128sites, comments/member names/shadowed declarations
excluded. Byte-base casts retain offsets and evaluation; plain local pointer
assignment wraps the result as void* to preserve original conversion behavior.
This is a compiler/semantic-tested hypothesis, not a proof of access width.

Two-source compile-only probe, immutable completed fresh-v3 worker sources:
void-arithmetic-transfer-v2.json SHA256
07abeecf4bf6bccac3816f00b4df306f5752555ec06c8015dd2c542e560668cd.
recordRaceReplayInputFrame child365/source
a78267bb634d8afcd5aaf59440788fff5e6fe2bd42f07d90a14492b262b52893,
compiled/frontendpasses44.152. saveRaceRecordReplayData child367/source
66ab5129be9bc526314da27012702463051b1a4e20189e7bd9f76779af77abf6,
compiled/frontendpasses36.705.0LLMcalls, target object hashes unchanged,
no game integration. Semantic tests NOT run; exposed-source mechanism transfer,
not unattended whole-pipeline or unseen acceptance. v1 retained separately:
save compiled but introduced9typed-pointer assignment frontend errors; v2 fixes
the generic result-conversion issue. Current54function frozen run unaffected.

Other observed families remain open: unknown stack aggregates/record views
(osMotorStop/updateRaceResultsFlow), source-only private record definitions
(RaceUiSnowboardTrailActor has only a struct forward declaration in headers),
and callsite ABI ambiguity. Header callback_task_scheduler.h explicitly omits
createCallbackTaskWithUserIdPreservingArgs because3/4argument callers disagree;
do not invent a shared prototype or claim missing-header recovery covers it.
Active frozen campaign itself repaired requestControllerPakSaveRead's OSMesg
conversion in a later source; prior status was intermediate, not a final failure.

User approved Ubuntu termination/restart; completed successfully. Old jobs gone.
Save93178 yielded buffered RecursionError in semantic_gradient._resolve_register
and exit1, no completion receipt. Diagnostic traversal now caps64levels/4096memo
nodes with explicit unknown/diagnostic-traversal-budget; no semantic-pass waiver.
_last_definition no longer copies whole trace prefixes.3000event regression.
Full1627passed22skipped (80462 terminal), focused scaling+gradient16passed.

Larger frozen fresh-v3 campaign LIVE8615: localGPT-OSS3calls per repair visit,
4functions per stratum per each of2disjoint batches,160work items per batch.
Preflight excluded560exposed names from192selection receipts; baseline copied
and integrity checked. No integration/reference bodies. Selection shortages
reduced selection to27per batch,54total. Selection is .cohort.json; batch1
observed inflight osStopThread/intake. See CURRENT_HANDOFF.md
for compact continuation state, command, hashes/paths and outstanding audits.

Validation blocked pending environment recovery approval: third consecutive
continuation with recurring Wsl/Service/0x8007274c. Elevated wsl --list --verbose
reports Ubuntu Running/version2, but guest process query58831 terminated exit1
with connection timeout. Old save handle93178 still running at host level;
guest state cannot currently be revalidated. Neither save-adaptive-budget-
original-v1 nor osCreateThread-balanced-panel-v1 has a completion receipt; the
latter also has no input receipt. Do not infer guest termination or duplicate
either launch. Local suite last1625passed22skipped; game validation still due.

Next requires user direction: permit terminating/restarting Ubuntu (would stop
ALL processes in that distro, including any still-running replay), or restore
guest access externally. No WSL/process restart performed. Once access returns,
inspect actual processes/receipts first, then validate latest bounded balanced
panel plus stride handoff, fresh/frozen replays and remaining failure inventory.
Goal incomplete; classifier/routing/code-test progress does not satisfy it.

Bounded stress generation now round-robins input families (memory, scalar
registers, pointer aliases, callee returns, filler) and retained target seeds
BEFORE the generation cap. Previously an eager/prefix memory stream could
consume4096 mutations and omit all call-return/register experiments. Existing
unlimited generation order remains unchanged; bounded generation remains
finite and can omit later dimensions within each family. No completeness claim.
Regression with10000memory locations and2seeds reaches all5families in the first
10mutations. Full1625passed22skipped (74019 terminal exit0).

WSL recovered without restart: /bin/true exit0, guest save process389 confirmed
live at33min. Scoped header search under include and src/ultra/libc found no
_Genld/ldiv declarations; no source-body lookup and no guessed prototypes.
Original-source osCreateThread-balanced-panel-v1 launch session16104 terminated
exit1 with Wsl/Service/0x8007274c connection timeout; no validation result.
Guest-side execution must be checked before any retry, not inferred from the
host transport failure. Intended0modelcalls; latest panel game replay still due.
Older save93178 remains live; no duplicate/replacement save job launched.

Fresh-v2 progress audit added: audit_fresh_progress_v2.py preserves all16
original checkpoint stages and separately binds post-fix osCreateThread,
__osSiRawStartDma and _Ldtob probes. Checks checkpoint/root/input/output/semantic
source identities and zero-call/no-integration scope. New receipt
failure-coverage-fresh-v2-progress-audit-v1.json SHA256
1a3c7a9685d416b9d66658e1839f809f763446da8c9c61baf559a5a4a1860e48.
Six tamper/positive tests pass. These are exposed-source repairs, not fresh
transfer successes; all causal_accounting_complete flags remainfalse. Current
WSL target/build pins explicitly NOT revalidated by this local receipt audit.

Environment interruption: WSL header lookup83799 and minimal /bin/true10048
both terminated exit1 with Wsl/Service/0x8007274c connection timeout. Existing
save session93178 still reports running but its guest process can no longer be
revalidated through new WSL calls; no terminal receipt exists. Do not call it
completed, restart it or restart WSL solely from that timeout. Host wslservice
exists; host memory CIM query was denied. No new game-side replay launched.
_Ldtob header lookup yielded no evidence; source-local header hypothesis remains
untested. Last full suite1618passed22skipped before progress-audit additions.

_Ldtob intake accounting: fresh-v2 attempt43 was a79byte empty m2c translation
unit, frontend-passing but no text symbols. Attempts44..51 already contained
~6.5KB real drafts with unresolved declarations. Error-count/context ranking
favored the empty source; later body-dependent normalization raised a generic
definition exception. This was a selection/handoff defect, not absence of any
available draft and not evidence that OSS could not solve it.

modelrepair now excludes no-text objects from frontend-quality credit and ranks
the explicit no-text diagnostic below real failing drafts in compile_error_rank
(shared by campaign intake/frontier selection). Body-dependent normalization
skips that diagnostic with 'needs a function draft'; it does not invent an empty
implementation. Two tests cover ordering and crash-free zero-call handling.
Campaign symptom accounting now names absent-compiled-function-body explicitly.

ldtob-absent-handoff-v1 TERMINAL: original empty checkpoint source retained with
explicit draft-needed log, no exception, no compile/solve. Separate NEW fresh
intake replay ldtob-ranked-intake-v1 TERMINAL via replay_intake.py,0LLMcalls,
base.c/target.s/target.o hashes unchanged; best359/source
16be6deec59abfefe576fd6810cfd2de38ba82c69257d7a25dda348448cf2f89,
6746bytes, noncompiling. First frontend errors now unresolved '? _Genld()' and
'? ldiv(s32 *, s32, ?)' declarations. ReceiptSHA256
0455c426b4433e54b9be7deaf98ff06f01314e69ca5df7db87b8cee8c5a146da.
Next: header/assembly interface recovery on this retained source, not another
repair attempt on the discarded empty unit. No semantic or exact claim.

SI semantic environment audit: semantic_assembly supplied no linked addresses
for SI_DRAM_ADDR_REG/SI_PIF_ADDR_RD64B_REG/SI_PIF_ADDR_WR64B_REG. Previously the
runner would allocate synthetic symbol regions, not device registers. New
solver/hardware_environment.py detects explicit relocation load/store operands
whose symbols are defined as *_REG macros in include/PR/rcp.h; retains header
hash, lines, macro text and assembly hash. semantic_lane refuses the unadmitted
environment before test construction and DeferredPanel preserves structured
environment_obligations. This is project-header-assisted positive detection,
NOT a complete classifier for numeric MMIO, other register headers, interrupts,
cache behavior or opaque device effects. No numeric address/effect invented.

si-hardware-environment-v1 TERMINAL, original checkpoint source,0modelcalls,
best349/source93cee2e9dc4e3d0edc69e016e72dd64eab406b83f3fe7381bf4457f5f28a5228,
compiled/frontend passed93.214/23byte differences. Semantic unavailable with
three explicit register obligations (RCP header lines799,802,807), not a pass.
Receipt SHA256f850ffe152910231a6450928b85b95c4ede773e6e48e0930509efdbbc47b9a81.
Remaining owner: device/callee-effect environment; byte repair is still separate.

Campaign summarize now routes hardware environment obligations and implicit
function declarations explicitly, without marking causal accounting complete.
Fresh-v2 original checkpoints re-audited to a NEW
failure-coverage-fresh-paired-v2-routing-audit-v1.json; historical source/result
stages remain unchanged. Postfix SI probes are separate, not retroactive solves.
Full1615passed22skipped (95526 terminal), then8focused hardware/accounting tests
passed after audit routing addition. Save93178 remains the sole old replay;
later stress-limit and stride-handoff real-game validation remains due.

Fresh-v2 __osSiRawStartDma causal replay: original source78e6330cae2aae73ed8cd5065658fca96327bd8a9f19ad075fb5420eb0d68d78
compiled93.214/23positional-byte differences but frontend rejected missing
__osSiDeviceBusy. Initial hypothesis that header recovery was wholly skipped was
incorrect: si-frontend-header-v1 terminal receipts331..334 show context/header
recovery DID run, then IDO syntax errors at three SI_* register externs after
macro-heavy internal headers were added. No model incapacity established.

modelrepair now activates existing header recovery from implicit-function
frontend diagnostics, including compiled children. Added alternative
compile_recovery.scalar_header_prototypes projects an existing unambiguous
primitive-scalar prototype without importing header macros. At most4 diagnostic
names; all header variants inspected up to13, saturation/conflicts/unknown types/
pointers/variadics/unspecified parameters decline. Header hashes and source hash
recorded. No inferred ABI/effects fact or header edit; ordinary compiler and
frontend must validate. Seven tests include conflicting signatures in one header.

si-scalar-prototype-v1 and final v2 TERMINAL compile-only original-source replays,
0modelcalls. v2 best344/source93cee2e9dc4e3d0edc69e016e72dd64eab406b83f3fe7381bf4457f5f28a5228
passes IDO and frontend, same93.214/23bytesdifferent. Added only header-derived
int __osSiDeviceBusy(void). No semantic/exact claim: SI DMA/MMIO and cache/callee
effects require separate environment accounting. Checkpoint/input receipts
bind the unchanged original source; baseline and game integration not requested.
Full1613passed22skipped (35314 terminal). Adaptive save93178 remains live,
predating later stress limits/stride routing; no replacement launched.

Stress work limits implemented (post adaptive-save-v1 launch): optional positive
integer max_total_steps/max_trials/max_generated in build_semantic_stress_panel.
All executions, including rejected seeds/mutations, count; final instruction
allowance clamps execution. Receipts retain attempted_cases, executed_steps,
limits, stop_reasons, examined_mutations and unattempted_seed_count. Mutation
construction uses a lazy iterator; compatibility _mutations remains eager for
other callers. Generation cap counts duplicates too and conservatively records
unenumerated dimensions. Prefix truncation may omit later memory/register/call
dimensions and later seeds; it is resource control, not completeness.

semantic_lane.Panel admits max(4*semantic_cases, seed_count) stress trials,
128*ORIGINAL per-case steps total caller instructions (1.28million by default),
and max(1024,64*semantic_cases) generated mutations. These are separate from
exploration budgets. Stress report enters panel identity and evaluated/unavailable
receipts; rejected/unattempted/truncated work becomes explicit execution debt.
Comparison work is still separate, and instruction accounting does not include
recursive callee instructions or all host-side trace/mutation preprocessing.
Do not claim a wall-time/memory bound for the whole pipeline.

Sixteen new stress-budget tests cover last-trial clamp, rejected-trial accounting,
lazy generation, unattempted seeds and invalid limits. Existing adaptive and
runner tests pass. Full1606passed22skipped (62550 terminal exit0). Prior handoff
tests also passed the recovered-child cases (8/8). Real-game stress/stride
activation replay remains due after old session93178 terminates; no new solve.

Continuation: adaptive target retry has three real-runner regressions (long-loop
retry retains inputs and both receipts, completed target does not retry,
unsupported target does not retry). Unavailable evaluations now retain phase
receipts directly as well as through Panel.report. Full1586passed22skipped
(8110 terminal), before adding the recovered-child variant of the handoff test.

modelrepair resilient normalization now offers up to4 existing byte-pointer-step
hypotheses per visited state when compilation/frontend pass and differential
status is observed_failure. It is disabled for compile-only, unavailable,
inconclusive and sampled-pass states. This reaches repaired-to-compilable
children that agentrepair's root-only deterministic search misses. Each child
gets normal compile/evaluation, parent linkage, source/diff hashes and hypothesis
receipt; no semantic or exact promotion from the generator. Normalization's
existing4round/frontier/dedup limits remain. No real-game activation result yet.

Adaptive save replay93178 still live when last inspected (~8min, process389).
It predates stride routing above; do not restart or claim completion. Audit
found a further resource gap: build_semantic_stress_panel.max_cases limits
SELECTED completed cases, not rejected trials or eagerly generated mutations.
Thus the earlier description of stress as simply case-bounded was incomplete;
the exploration aggregate limit does not bound whole-panel construction.
Add explicit stress work/generation accounting before claiming runtime bounded.

Long-loop work allocation: explore_coverage accepts optional max_total_steps,
counts every trial's caller instruction_count and clamps the final trial to the
remaining allowance. Reports executed_steps/max_total_steps and an explicit
total-budget stop reason; never calls exhaustion nontermination/infeasibility.
Default low-level callers unchanged. Five tests cover aggregate accounting,
last-case clamp and invalid budgets; focused73passed.
Full1579passed22skipped (37387 terminal exit0), before the final addition of phase
receipts to panel identity hashing. Only experiment93178 remains live.

semantic_lane.Panel uses an initial16*per-case-step aggregate budget. If no
target run completes and step_limit occurs, one bounded retry uses up to8 retained
step-limited target inputs, up to32trials, up to16x per-case steps (cap1million),
and the remainder of128*original-step total allowance. At default10000steps,
exploration phases together allow at most1.28million caller instructions.
Both phases retained in report and panel identity; prior noncompletion explicit
debt. Stress/comparison use the same effective per-case limit. No candidate-led
input selection or resynchronized acceptance. Stress work is separately bounded
by its existing case limit, not part of the exploration allowance.
Original-source save-adaptive-budget-original-v1 LIVE session93178; no automatic
completion/stride repair claim yet. The normal probe has0deterministic candidate
budget, unlike the targeted previous experiment; routing activation remains due.

Save long-loop diagnostic completed: save-long-loop-v1.json, four explicit
header-declared slots0..3, one memory seed,120000instruction budget, opaque OS
calls with explicit successful FindFile/ReadWrite returns. Target returns on all
four cases in85299instructions. Candidate fails all4: slot0 persistent-memory
disagreement before ReadWrite, slots1..3 candidate-only buffer overrun. Thus the
old step limit concealed a real candidate stride error, not target nontermination.
Receipt SHA256c944f57c5916d5413ffbd75d2331725d6633c5b35a3230c291ca1345102e92b3.

Existing rewrites.byte_pointer_step_rewrites excluded strides<16. Candidate
checksum cursor is s32* +=4 (16bytes), target self-addiu advances4bytes. Removed
the large-record heuristic; still requires positive matching target stride and
typed nonbyte/nonvoid local. New regression for4byte stride; focused74passed,
full1574passed22skipped (56061 terminal exit0). No probe/test worker remains live.
Targeted --byte-step mode in save_long_loop_probe compiles the unique existing
residual proposal with parent linkage and repeats the same four cases. Separate
save-long-loop-byte-step-v1 TERMINAL (50792 exit0),all4cases passed. Best diagnostic
attempt323/source647794cafbbbee3e7e969875c17574d20e8045f3ee930136f41504d123cd9d07,
compiledscore81.625. Receipt SHA256
d15bbc8f1a6adfff5eee6cc6eb5c324168c9928f0cdb7bd1e4b0afb45199a142. This is
targeted diagnostic activation, not yet an unattended original-source solve.
Normal semantic budgeting/repair routing still must handle long loops efficiently.

Runtime callback transport: project_headers.active_declarations accepts the
bounded one-level callback-pointer syntax emitted by the configured header AST,
validated through type_transaction.signature. Unspecified/variadic/nested/double-
pointer forms decline. dag prototype_info records callback transport as a pointer
word plus explicit debt; this does NOT admit indirect callback execution.
Six new active-header tests; full1573passed22skipped (35906 terminal exit0).

osCreateThread-runtime-callback-v1 TERMINAL, best320/source
1e0f1fe1d8302ba3a522bed076c2374bfc7b87170357b655aa6cbb0097c4bcb6,
compiled55.488,227positional bytes different,64/64sampled passes,0model calls.
Finite synthetic inputs, header extents, opaque __osDisableInt/__osRestoreInt,
callback and stack-word domain limitations remain. NOT universal proof/exactness.
Receipt SHA25623c9cd98a583853381dabb81f0c1053b15733f5da88866ef3060aed9706f31b7.

Save measured-memory replay90286 TERMINAL exit0, best280/source
058ac35d0671c126fcf5082ee7902f4ad6126476bdd86caa45b803084839e78d.
Automatic measured extents admitted gGameSaveDataBuffer123872bytes and
gControllerPakRetryCounts4bytes, no declines. Original default-memory fault
cleared:5000target trials now4998step_limit/2memory_fault (was5000memory_fault).
Still semantic UNAVAILABLE/no completed target cases. Limit is10000instructions;
the long checksum loop needs a higher per-case limit, not5000more near-identical
attempts. Next bounded diagnostic: few explicit slot0..3 inputs with enough
steps to complete checksum, retaining opaque OS calls/invalid-index debt. Do not
call step-limit exhaustion nontermination or a candidate mismatch.
Receipt save-measured-memory-original-v1.json SHA256
0872d37cfbbe20de3077d42f128ab545cd20f29f4befb744e752560bb45b0fa2.
No worker from these runs remains live.

Void-pointer expression dialect repair: repair_context.normalize handles target
IDO Unacceptable operand/Bad operand diagnostics for explicit
(void *)(plain_void_pointer_parameter +/- integer_literal). Inserts unsigned-char
byte view, preserving one evaluation and public type. Typed pointers, side-effecting
amounts/operands, missing parameter identity and local shadows decline. Existing
compound-assignment repair remains separate. Seven focused tests passed;
full1567passed22skipped (14226 terminal exit0).

osCreateThread-void-expression-v1 TERMINAL, best299/source
93f8dd5660cd8b1120a7667cd2216f9597b210d33681341d5f9e5d3e29284962,
original checkpoint source,0model calls,COMPILED/frontend passed,score55.488,
227positional bytes different. Compile-only, not semantic success or exactness.
Receipt SHA256d67a06a8d57350b6e277c1ec4a48cc3fcd8455de0a70fd397445e7ec64d47744.
Separate original-source semantic replay osCreateThread-semantic-v1 TERMINAL,
best309 same source. Semantic UNAVAILABLE before execution: active prototype has
unsupported complex/unspecified ABI. Type-transaction spelling lock now handles
callback parameters, but runtime ABI admission is a separate remaining gate.
Receipt SHA256d261737ab3395cf6220cdf3a3d23abe8f90cf5edf1d2e9a707c7f86a1cdd3a3b.
save measured-memory replay90286 remains confirmed LIVE. No duplicate save run.

partial_word_fields.propose now handles compiler-rejected unkHEX stores on the
first plain typed pointer parameter. Requires source-hash-matched target-compiler
measurement, unique containing u64/s64 field with valid owner extent, unique
target sw at the exact param0 offset, one source assignment and no other uses
of that unknown member. Mutated/address-escaped root, missing/ambiguous views,
nonword stores and read/mixed uses decline. Rewrites the lvalue to an explicit
u32 byte-addressed store and casts the RHS once, retaining statement order and
public signature. Candidate hypothesis, not field-type or semantic evidence.
Wired into resilient normalization on missing unkHEX frontend diagnostics with
parent/source/measurement/store receipts. Six focused regressions passed;
full1560passed22skipped (42058 terminal exit0).

osCreateThread-partial-word-v1 TERMINAL, best290/source
21e8f89ee0ca4d95ee71eebf10c604f305542ded39d0c072a9644bc9e5afc754,
zero-model compile-only replay of original fresh-v2 source. Six frontend errors
cleared; FRONTEND PASSES but IDO rejects line19 "Unacceptable operand of X."
Receipt SHA256ab63aa844874e2132bb2e0b8f7ce1ead564b7978c57c73240c116af75a850a71.
Remaining expression is void* arg4 -0x10 inside the saved stack-pointer word.
This is another byte-address dialect gap, not justification to change the header
layout or declare successful compilation. Existing void-pointer normalization
handles +=/-= statements but not this expression. No semantic result requested.
Save measured-memory worker90286 still confirmed LIVE.

osCreateThread six-field causal evidence confirmed against extracted target
dataflow and existing target-compiler header measurement (no reference C body):
sw instructions24/25 address param0+0x38/+0x3c; instructions34/35 address
param0+0xf4/+0xf0; instructions40/41 address param0+0x100/+0x104.
Measured OSThread owner_size432: context.a0 u64 offset56 width8, context.sp u64
offset240 width8, context.ra u64 offset256 width8. Thus these six unknown labels
are candidate partial-word views of three existing64bit fields, not six missing
record members or an unknown overall layout. Evidence receipt:
nonmatchings/osCreateThread/type-constraints-layout-8c7ada8b4050fd0a159305427aa4cc366d0d9682360b1f39732ca6e5c6dafdad.json
under WSL repo. Source binding is callback-parser-v1 best284 cited below.
Next mechanism: compiler/header-bound partial-word views for already typed roots,
or a controlled paired-word coalescing candidate; require exact offsets/width,
source-family closure and side-effect/order checks, retain ABI and recompile.
Existing void-root constraint planner cannot activate on this typed root;
wide_return_repair handles a different class (callee result words), so do not
conflate these. No osCreateThread semantic or byte-exact success claimed.
Save-memory worker90286 remains LIVE as last polled this continuation.

osCreateThread callback-parser replay TERMINAL exit0, best284/source
c04c84fd1b64ea7c97ccb5b4721a938ba3dfdb6c4a8e76a2268a7071dbd74586.
Original frozen fresh-v2 source replayed with0model calls/compile-only; parser
crash gone. SDK header recovery now reaches6missing OSThread fields: unk38,
unk3C,unkF0,unkF4,unk100,unk104 (saved-register word views). Still NONCOMPILING.
No semantic claim. Byte-view redraft correctly declined changed public types;
constraint planner declined because roots already OSThread*, not void*.
Receipt osCreateThread-callback-parser-v1.json SHA256
d651e09a7df18dfdc5318e168987946c5193bd10739b45295f9b5a9ff693e4ad.

Reusable replay_checkpoint_source now validates terminal checkpoint/source SHA,
checks heldout membership, emits separate input provenance, refuses existing
outputs and uses a development DB with no cross-DB parent IDs. Optional compile-
only permits compiler-gap probes without semantic exploration. Full1554passed
22skipped (31178 terminal exit0). One regression
test validates hash and terminal-state rejection. The first osCreateThread v1
receipt predates separate input-provenance emission; its input binding is the
original fresh-v2 checkpoint/source cited above, not a fabricated side receipt.
Save measured-memory worker90286 still confirmed LIVE; do not restart it.

Callback ABI spelling support: type_transaction.signature now balances the
outer parameter list and splits only top-level commas. Ordinary one-level
callback pointers have canonical return/parameter tokens with names ignored;
unspecified/variadic lists, pointer-to-pointer, callback arrays and nested callback
declarators still decline. No typedef-equivalence or inferred callback contract.
validate uses the same balanced definition finder and compares the actual
definition, rejecting a changed definition hidden behind a matching prototype.
Seven new tests + existing transaction/definition tests27passed. Actual saved
osCreateThread signature now parses as void(OSThread*,s32,void(*)(void*),void*,
void*,s32); remaining SDK/header declaration mismatches still need replay.
Full1553passed22skipped (58900 terminal exit0). Save measured-memory replay90286 remains confirmed LIVE;
no final target-completion or semantic claim yet.

Fresh-v2 causal triage distinguishes two operational parks previously sharing
"requires one ordinary function definition". osCreateThread saved source
a9237cd0d9760dbc431cdc7781144215053571b0c6d484e9e86329939e5cab16
contains a definition with void(*entry)(void*) callback parameter. The old
repair_context.definition excluded all parentheses inside parameters. It now
balances parameter parentheses while preserving Match groups/spans used by
existing edits; prototypes, duplicate/missing definitions, unbalanced or K&R
declaration tails still decline. Seven tests plus compile-recovery suite31passed.
Actual saved osCreateThread source now parses; type_transaction.signature STILL
returns None for that callback signature, a distinct remaining ABI machinery gap.
No compilation/semantic success claimed and no callback ABI invented.

_Ldtob source325a6d34e74a69034223717e5679c7e2d92f88b9ae60d882903083244dada022
is only common.h plus "file is blank because m2c failed to decompile function".
Its frontend pass/no-text compiler failure is not a real C candidate. It requires
fresh draft recovery and explicit missing-body routing, not nested-parameter
parsing. Historical cohort parks/receipts remain unchanged. Remaining fresh
failures include unknown globals/aggregate views, stack placeholders, SDK public
type spelling and missing internal prototypes; their existing stage audit remains
causally incomplete. Full1546passed22skipped (17608 terminal exit0);
save replay90286 remains confirmed LIVE.

Header extent activation: semantic_lane.Panel now measures up to8 sorted linked
symbols declared by included headers, recording global_extent_admission with
probe object hashes/receipt paths, declines and omitted names. Appends validated
MIPS_DIFF_EXTENT metadata to the target assembly before panel exploration and
identity hashing. Program.symbol_sizes consumes this metadata; paired execution
unions target/candidate sizes, providing identical mapped regions to both.
Metadata requires explicit linked identity, positive bounded size, and rejects
conflicting extents. Header-assisted memory is explicit semantic debt; synthetic
contents, callees, valid-input domains and universal equivalence remain unproved.
This is POST fresh-v2 snapshot. Focused75passed; full1539passed22skipped
(10349 terminal exit0).
Original-source save-measured-memory-original-v1 is LIVE session90286; no result
yet, so do not claim the checksum loop now completes or the candidate passes.

Fresh v2 worker72065 TERMINAL exit0. Two8function cohorts each report1object_exact,
with batch1 parked3/stalled4 and batch2 parked1/stalled6. No integration requested.
Controller-level exact names openRaceTypeSelectFlow and routeRaceCharacterSetupFlow;
Source-bound stage audit completed:2accepted_object_exact,2sampled_pass_nonexact,
7not_compiling,1frontend_rejected_or_unavailable,4parked; zero actual model calls.
failure-coverage-fresh-paired-v2-stage-audit.json SHA256
442f4cdd8bb57eb308e0f3fee5ef7de0caf307ef3e1d6bac6a2c5cc135c121d3.
Not yet a complete causal or whole-game audit. Fresh failures require triage rather than declaring
the historical fixes general enough. Both new memory changes absent from snapshot.

Linked memory extent primitive: SymbolTable retains linked_names only from
explicit known_addresses (not D_HEX parsing). _seed_memory permits requested
extents above64KiB only for these names, bounded to8MiB per object as a resource
limit, rejecting address wrap and intersections with scratch/stack/synthetic
regions. Explicit linked aliases still use shared address-keyed memory. Existing
synthetic stride and case-write caps remain; initialized-data handling unchanged.
Six tests cover123872byte extent, alias visibility, end-bound faults, lack of
linker identity, wrap/resource limits and synthetic/reserved collisions.
Focused74passed. This is NOT yet automatic header-extent admission: the adapter
must bind measured header objects to target symbols and feed identical extents
to both programs, exploration and diagnostic replay with provenance/debt.
Full1538passed22skipped (27188 terminal exit0). Fresh cohort72065 still confirmed live; its frozen
snapshot predates both header-size measurement and linked-memory extension.

Global extent measurement added to existing type_constraints.measure via optional
global_symbols (default empty, existing callers unchanged). Header-only AST must
contain one declaration per requested name; target compiler emits sizeof(name)
constants after the existing layout packet. Up to64 unique identifier names;
no arbitrary C expressions, pointee dereference or candidate declarations.
Report global_extents binds sizes to header declaration and probe object/recipe.
Incomplete/unavailable types fail compiler measurement rather than guessing.
This measures objects; it is NOT wired into semantic memory activation yet.
Thirteen focused type-constraint tests passed including bounded probe names.
Full1532passed22skipped (84432 terminal exit0).

Real save probe measured gGameSaveDataBuffer=123872bytes, GameSaveData[4].
Receipt in WSL nonmatchings/writeControllerPakSave:
type-constraints-layout-229fa54795b39ed72b02acf5ad82d9ee36e8d6d4942f803a94c8632b67ef941b.json.
That exceeds the64KiB synthetic stride cap: next address-aware activation must
distinguish fixed linked address intervals from synthetic allocation and retain
overlap/alias and initialized-data consistency. Do not merely raise every region.

Fresh v2 worker72065 still LIVE, batch1 intake started. It reports
openRaceTypeSelectFlow object_exact (score100), osSetIntMask parked, and other
pending candidates; these are intermediate controller results, not final audited
transfer totals. New global-size measurement is POST this frozen cohort snapshot.

Fresh transfer validation resumed: fresh_run_v1 now accepts --version and
--model-calls, preserves v1 defaults and refuses existing experiment paths.
Prior fresh-cohort databases join historical attempt/selection exclusions.
Four launcher option tests passed. New frozen v2 run is LIVE session72065,
--version2 --model-calls0,40-work-item budget per existing paired controller.
Preflight fresh-cohorts-preflight-v2.json: baseline-copy integrity ok,2113functions,
72041evidence,0attempts/0inference;544 exposed names from historical databases
and189 selection receipts excluded. External/unrecognized exposure remains debt.
This run includes the endpoint/cursor repairs absent from frozen replay v8.
Do not overwrite or restart the live run; inspect its actual stage counts and
audit source-bound outcomes when terminal. Full test worker72517 terminal exit0:
1531passed22skipped. Cohort worker72065 still confirmed live afterward.

Save-memory investigation: include/game/save_data.h declares an array typedef
GameSaveDataBuffer and extern gGameSaveDataBuffer, with compile-time size checks.
Program.symbol_sizes currently derives minima from relocation addends/data,
not header object sizes; _seed_memory has a64KiB synthetic stride cap. A generic
header/compiler-backed extent adapter and addressing policy are still needed;
no per-function memory enlargement or semantic certification was added here.

Frozen v8 TERMINAL AUDITED (59896 exit0). All16 rows,zero execution_errors,
zero model calls,all pins and immutable baseline unchanged. Audit stages:
3accepted_object_exact,1not_compiling,2differential_disagreement,
2semantic_untested_or_unavailable,2parked,3sampled_pass_with_debt_nonexact,
3sampled_pass_nonexact. All3 existing exact matches retained; noncompilers3->1
relative to v7. No new exact match. Causal accounting remains incomplete.
Audit failure-coverage-fixes-replay-v8-audit.json SHA256
64c20117eb48d86ab9eace41036eb6d15328d373326e96bf06e9727e7ce22ee6.

Post-snapshot save-byte-cursors-original-v1 TERMINAL (37076 exit0),best273/source
058ac35d0671c126fcf5082ee7902f4ad6126476bdd86caa45b803084839e78d,
compiled score81.588,358positional bytes different,zero model calls. Semantic
UNAVAILABLE: all5000target trials memory_fault. Initial a0=0 case reaches the
checksum loop and faults at0x800ecaf0 (gGameSaveDataBuffer+0x100); the harness
mips_differential._seed_memory defaults globals to0x100 unless explicitly sized.
Do not treat these target faults as candidate mismatches. Larger input indexes
also fault and need separate valid-input/coverage accounting. Next investigate
header/compiler-backed memory extents and the synthetic64KiB stride cap before
adding per-function hardcoded regions. Opaque osPfs calls remain explicit debt.
Receipt SHA256f9a5c34cdf039e9df0c3403bb486ea469b9605035cd40fe35db69e5b78c4ebf6.
No experiment/test worker from these two runs remains live. Fresh stratified
post-fix transfer validation is still due; fresh_run_v1 currently hardcodes v1
paths and requires versioned, exposure-filtered reuse rather than overwriting.

Post-v8-snapshot byte-cursor candidate repair: m2c_byte_view.unknown_local_cursors
recognizes M2C_UNK locals whose complete use family is one named primitive-extern
address seed, literal positive += steps and explicit u8 byte-field accesses.
It coordinates a u8* declaration with a u8* address cast, preserving steps and
access expressions. Unknown/volatile/pointer extern storage, escapes, duplicate
or mixed declarations/assignments, bare dereferences, other access widths and
effectful increments decline. This is a source-constrained C hypothesis, NOT
binary-derived type/extent evidence. It does not define the backing object.
Resilient modelrepair normalization emits unknown-byte-cursors candidates with
byte_cursor_hypotheses and ordinary parent/compiler/semantic receipts.
Eleven new tests cover activation/idempotence/declines; focused25passed, final
full1527passed22skipped (93159 terminal exit0). Original-source save replay
save-byte-cursors-original-v1 is LIVE session37076; no terminal result yet.
Read-only development DB check shows attempt273 from unknown-byte-cursors
COMPILED at score81.588. This is an intermediate attempt, not the final selected
candidate or a semantic result; target exploration is still running.
Frozen v8 is separately LIVE session59896,14/16 recorded rows with all3 existing
exact matches preserved so far. Neither post-snapshot extension is inside v8.

Post-v8-snapshot endpoint extension: address_units.address_only_globals now
admits the closed `(u32) local < (u32) &endpoint` source shape when target sltu
has that named address as its RHS. The unsigned casts/operator are preserved;
signed tests, other operators, address offsets, scalar locals, mixed uses and
wrong/clobbered binary operands decline. Named HI/LO scheduling window is now
16 same-block instructions, still rejecting intervening calls/register clobbers.
This accommodates the save endpoint's nine-instruction scheduled separation.
No numeric address, array length or common-object pointer-ordering claim added.
Ten focused tests passed; full suite1516passed22skipped (39875 terminal exit0).

save-unsigned-endpoint-original-v1.json TERMINAL (61910 exit0), best265/source
385c552eefff37167eddf79581add31900ea35eff24db38a6fe02837abd080e8,
zero model calls, NONCOMPILING with11 frontend diagnostics. Endpoint is now
declared; existing recovery also emits a candidate s32 save-identity array.
The two M2C_UNK byte-copy local declarations remain, plus their cascading uses
and incompatible pointer assignment. Next repair must coordinate byte-pointer
views and address casts without blindly typing every unknown as a byte pointer.
Receipt SHA2569546abab6a0458daa09aacb4410e7a9ed9b3c12b043047fcbc1bbec161e68449.
Frozen v8 remains LIVE session59896; latest saved5rows includes osSpTaskYielded
exact, no execution_error rows so far. This endpoint extension is NOT in v8's
immutable snapshot. Await terminal audit before reporting cohort totals.

Latest continuation: both original-source probes below are terminal receipts;
their former sessions53130/77192 no longer exist. Multiplayer now compiles and
passes frontend (best256/source6afb858367ba9d6e9ba4e50c93790a1fb776a82ae26ab86ec641b9326228092f),
score59.588,2700 positional bytes different,zero model calls. Semantic validation
is UNAVAILABLE, not passed: all5000 target exploration trials memory-faulted.
Examples progress from invalid gCurrentGameTask to missing camera-array memory
at0x8011236c and out-of-range synthetic player counts. Opaque callbacks/callees
and unknown enqueueSoundEffect arity remain explicit execution debt. Receipt:
multiplayer-void-byte-step-original-v1.json SHA256
f5dd6656161a6662bedb56a1ee1b96d57c782eb8d34611c8a4ed01b5f58c85bd.

Existing m2c_context.indexed_externs now recognizes M2C_UNK extern placeholders
as well as legacy '?', but does not infer array elements from M2C_UNK pointers.
byteview_redraft feeds at most4 typed-pointer array hypotheses through one
additional valid-syntax m2c call, retaining headers/callee prototypes, checking
public ABI again, and recording indexed_extern_hypotheses. No extents inferred;
no declaration is admitted as binary evidence. Tests cover successful activation,
callee/header retention, compile failure and ABI-change rejection.
save-indexed-feedback-original-v1.json best261/source
8c0a34c5559752f08a734a5a823865020f3f14d435235389a779c1e10e1ca8dd
recovers OSPfs gControllerPakHandles[] and s32 gControllerPakFileNos[] and their
indexed uses automatically. Still NONCOMPILING with12 frontend diagnostics:
unknown save-record storage, endpoint and two byte-copy pointer locals. No
semantic result. Receipt SHA256
64ca5e60364a2647a8b22ee66cdd7cea93a92766d521cbebb7ed73524233832c.
Final full suite1515passed22skipped (session62335 terminal exit0); focused
59passed5skipped. Frozen v8 is RUNNING, session59896, with a fresh baseline-copy
database and pinned snapshot. First __osDevMgrMain row evaluated/unavailable;
no final cohort count or audit yet. Do not restart this worker. Frozen v8 is an
activation/regression check, not fresh transfer. Next: terminal v8 audit, remaining
save-storage/byte-loop recovery, and callback-memory environment reconstruction.

Endpoint and dead-pointer cleanup now cover two multiplayer compile failures.
address_units.address_only_globals admits comparison-only symbolic endpoints
when a named HI/LO address and binary beq/bne comparison exist. A numeric address
may remain explicitly None (D_801124A0 is NOT in symbol_addrs); no address is
parsed from its name. Indexed byte views still require independent symbol-map
identity. HI/LO scheduling allows up to8 same-block instructions, declining any
intervening call/register clobber. Source endpoint uses must be closed equality/
inequality comparisons against plain pointer locals. No extent/type inferred.

pointer_spill_cleanup.propose removes only private pointer locals used solely
as standalone assignment destinations whose RHS is another uniquely declared
nonvolatile local pointer. Reads, escapes, globals, calls, dereferences, side
effects, qualifiers or unsupported syntax decline. Wired to resilient chained
normalization with parent receipts. Multiplayer sp78 copies disappear without
changing calls or inventing a common pointee type.

Original-source probes: endpoint-spill-v1 best228 and v2 best234 remain at one
frontend error because endpoint is missing from symbol map. Symbolic endpoint
v3 best243/sourcebc09ad3d... PASSES FRONTEND but IDO rejects void* +=0x60c at
line161. This is not compilation success. repair_context.normalize now handles
target-compiler "Bad operand type for" with a closed plain local void-pointer
+=/-=literal byte-view rewrite, preserving statement position and operand.
Typed/volatile pointers or side-effecting amounts are not rewritten.
Full suite1511passed22skipped (6674 terminal exit0). Current replay
multiplayer-void-byte-step-original-v1 is LIVE session53130; no final result yet.
Earlier probe sessions92708/74330/30085 terminal. Next inspect actual compiler
and semantic outcomes, not merely the now-clean frontend.

Bounded callee-interface feedback now reuses m2c on uniquely resolved extracted
callee assembly, then feeds only a structured fixed-word prototype back into a
fresh caller draft. callee_prototype_repair.hypotheses handles at most2 missing
M2C_UNK declarations, callees<=256instructions/32768chars, scalar word parameters
<=4 and scalar-word/void return. Existing header declarations take precedence;
unresolved, pointer/FP/wide/unprototyped interfaces decline. m2c_input supports
validated structured function_prototypes (no arbitrary context C), hashes the
wrapper and logs the declarations. Caller public ABI lock is rechecked after
redrafting. These are CANDIDATE interfaces, not admitted ABI/effect contracts or
evidence/inference DB facts. No reference C bodies or header modifications.

enqueueSoundEffect extracted32instructions saves a0/a1 then uses signed low
halves; only nested reserveSoundEffectQueueWriteIndex reads no entry arguments.
Header-only m2c independently emits s32 enqueueSoundEffect(s16,s16). The fresh
caller now has consistently2argument calls, instead of bogus2/3/4argument calls.
multiplayer-callee-prototype-original-v1 TERMINAL (81911 exit0),best222/source
67e783cc8d200fb42d47ef2f698a73793f856fff5f3171d8c6af74e4efa08e07,
stillnoncompiling,4->2frontend errors: M2C_UNK D_801124A0 and u8*sp78 assigned
RaceCamera*. No semantic/exactness claim. Receipt SHA
9362963cad303a1ba0b8661f7d1602bb1d6bd73909d2426fa4b093befe32adb7.
Six adapter tests cover structured context/injection/unsupported types; six
callee tests cover provenance, header precedence, bounds and unresolved types.
Full suite1497passed22skipped (44590 terminal exit0). Remaining repair work is endpoint address typing and
side-effect-safe handling of the unused compiler-managed pointer temporary.

Internal type fallback now reuses header-aware m2c when assembly-only byte
lowering declines, not only when the public ABI differs. Reason and first-draft
metadata are retained; the retry still must satisfy the same public ABI lock
and macro validation. No callback prototype was invented: existing RaceCamera
header declares update as void(*)(void), and header-aware m2c uses that member.
Two tests cover internal callback retry and rejection of ABI-changing fallback;
31focused and full1485passed22skipped (68794 terminal exit0).

multiplayer-internal-header-retry-original-v1 TERMINAL (87318 exit0),best219/source
9cd0803bcef847290482fb3cebbacc07aee08e1ef495612c8a34a26f1ff6f805,
stillnoncompiling. Frontend20->4errors: unknown M2C_UNK enqueueSoundEffect return/
parameter, unknown endpoint D_801124A0, u8*sp78 assigned RaceCamera*. There is NO
enqueueSoundEffect declaration in include/**/*.h. The generated calls carry
inconsistent2/3/4 arguments, so replacing its prototype alone is insufficient.
sp78 has only assignments (5byte-pointer assignments and1camera-pointer); unused
spill cleanup is a candidate opportunity, not authority to remove side effects.
Endpoint is comparison-only (&D_801124A0), outside indexed-global view admission.
These are distinct next evidence obligations; no semantic pass claimed.

Multiplayer transfer identified another independent activation bug:
m2c_byte_view.lower previously classified EVERY externs32 as a pointer root,
so scalar D_8010ADE8 caused whole-draft rejection. Roots are now selected only
from void-local address assignments, indexed dereferences or indexed field
macro bases. Unrelated scalar declarations/arithmetic/other-function uses stay
unchanged. Mixed uses of an ACTUAL selected pointer root still decline.
Regression covers scalar coexistence with both indexed roots and local-only
fields;29focused tests passed. New original-source replay
multiplayer-selective-byte-roots-original-v1 is terminal (29731 exit0),best216
source2edc1725... stillnoncompiling. Guard now reaches the distinct unsupported
field spelling M2C_UNK (**)() at var_v0_4 offset0x2c. Do not rewrite this function
pointer as void** or invent a prototype: callback ABI evidence is the next need.
Full suite1483passed22skipped (80408 terminal exit0); no live test/experiment remains.

stats-address-view-original-v2 is TERMINAL (46586 exit0; do not reuse its old
WSL PID384, which can be recycled). Best208/source
7aa4405c64a6c2a5bd18775c44d7350876e76e38963fa2d1abc003d927cdeace,
compiled/frontend,score67.031,1204positional bytes different,zero LLM calls.
Semantic panel8passed56failed; first retained feedback differs at sprintf output
stack address0xfec vs0xffc, with identical format "%d" and integer123. Opaque
callee address equality is NOT the repair objective. Source has s8sp6C/sp6D,
explicit '?' and NUL scalar writes and sprintf(&sp6C,...), suggesting missing
buffer reconstruction; do not claim a C extent from stack spacing or synthesize
formatter behavior as binary truth. Existing stack_buffers refuses this shape
(nested call arguments and non-address-only locals); source_object_bounds only
handles explicit byte accesses, not formatter capacity requirements.
Exploration attempted5000cases:4969memory faults,31returns; selected completed
coverage292/411instructions,36edges/28branches. Invalid synthetic asset handles
inside concrete getRelocatableHeapBlockBase dominate examples. Retained64case
results do not discharge this environment/coverage debt. Receipt SHA
42adac8807258c2829525382a190a8feb86a01f829dba0cdc3ffcbb0d8ea4ea2.

Transfer smoke tests on both remaining ORIGINAL frozen noncompilers are terminal:
multiplayer-local-byteview-original-v1 best210/source2edc1725... stillnoncompile;
save-local-byteview-original-v1 best215/sourceca0efb90... stillnoncompile.
Multiplayer: callback/header/global context plus negative unknown-field syntax.
Save:14frontend errors, M2C_UNK globals FileNos/Handles/SaveExtNameBytesEnd/
SaveFileIdentity and two M2C_UNK* byte-loop locals. These are exposed-source
transfer checks, not fresh functions or proof that those classes are supported.
No live probe/test handles remain. Frozen v7 still authoritative cohort summary;
later draw-stats compilation is dev-only until the next frozen replay.

Post-v7 local typed-byte activation fix removes the unrelated requirement for a
global-pointer hypothesis. Supported fields remain required; ABI lock and macro
validation unchanged. Three new tests cover local-only fields, no-fields decline
and unsized-void decline. stats-local-byteview-original-v1 from ORIGINAL frozen
source: best198/source a9ee3fdcbeac542c1fc00bd76e9ac231236ac8a2364cf69509a3ff20aa47292a,
frontend errors18->1: unknown M2C_UNK gCourseSelectStatsPlayerMarkerLayout.

That global has no header declaration and indexed access identity is absent from
direct-width mining. New address_units.address_only_globals (called by
compile_recovery.globals_variant) offers an incomplete u8[] view only with symbol
table identity, complete same-block adjacent HI/LO address pair, and closed
address additions into void-pointer locals used by explicit typed byte views.
Replaces &array by array decay, preserving index arithmetic (including bitwise
masking); no bound or field type is inferred. Ambiguous/other-function uses,
missing linker/HI-LO and existing header declarations decline. Two tests cover
these gates. Full suite1482passed22skipped (94575 terminal exit0).
stats-address-view-original-v1 declined on a bitwise '&1' index; corrected matcher
leaves that arithmetic intact. v2 replay is currently live (session46586, WSL
PID384 confirmed); no final compilation/semantic result claimed yet. Historical
frozen-v7 cohort remains3noncompilers; no fresh-cohort improvement claimed.

Frozen replayv7 is TERMINAL and AUDITED (worker68993 exit0), no execution errors,
no LLM calls, baseline and all pins unchanged. Stages:3accepted-object-exact,
3noncompilers (was4 in v6),1differential disagreement,1semantic unavailable,
2parked,3sampled-pass-with-debt nonexact,3sampled-pass nonexact. Gate moves from
noncompiling to64/64sampled pass-with-debt; all3 exacts preserved. Remaining
noncompilers: drawCourseSelectCourseStats,updateMultiplayerCourseSelectMenu,
writeControllerPakSave. Audit failure-coverage-fixes-replay-v7-audit.json SHA
ec244f812d712751195b420227e1038fe548ac958ca9294672815d01892b6685.
This is exposed-source integration validation, not fresh transfer or causal
closure of all failures; audit explicitly keeps causal_accounting_complete=false.

Next confirmed gap: drawCourseSelectCourseStats typed-byte recovery declines
"no supported typed-byte reconstruction" because it requires BOTH fields and
global/opaque hypotheses. Header-aware assembly draft was independently checked
after v7: m2c return0, public ABI equal,16supported fields,0hypotheses (first
examples temp_s3 s16* offset32). The absence of a global-pointer hypothesis must
not veto valid local typed fields. Fix the activation predicate with regressions
and replay the original source; do not invent globals merely to satisfy a guard.
No guard edit yet; frozen v7 source and historical receipts remain intact.

Indexed-table address recovery now lives in indexed_address_repair.py, wired
into fresh compile recovery and resilient normalization. Header compiler
measurements bind the index global/member offset, width and signedness; target
witnesses bind its shift/byte stride to either a complete HI/LO table address
formed in a direct-call delay slot or a HI/shift/add/LO scalar load. Requires
unique witnesses and source declarations; mutated indexes, stale measurements,
wrong signs/widths/offsets/strides, incomplete relocations and intervening calls
decline. No table extent or universal semantic claim. Seven focused tests cover
these guards, other-function preservation, fresh measurement and failure routing.

The first original-source replay gate-indexed-address-original-v1.json compiled
as attempt175/source d2c96df1...,score64.174,20passes44failed. It exposed a
controller composition gap: initial normalization children were never normalized
again, so fixing the call address did not compose with stack-object repair.
modelrepair now chains up to4 normalization rounds, deduplicating source before
compilation and selecting only UNEXPANDED candidates for follow-up rounds. The
existing diverse frontier and byte/semantic champions remain; a cap is logged
as a limit, not a fixed point. Three tests cover chaining/lineage, bounded work,
and cycle deduplication. This runs for initial states and model-generated children.

gate-normalization-chain-original-v1.json starts from ORIGINAL frozen-v5 gate
source with no assisted edits and no LLM calls. Best194/source
d277cdcc1db54b836b9b846c4713f4baf5f463ca30ecfd5ffd7a2995d2c3adff:
compiled/frontend,64/64pass-with-execution-debt on samepanelc1f993c9...,
score89.126,131positional bytes different. All earlier gate assisted address and
object edits now have automatic mechanisms. Opaque callees/object validity and
sampling debt remain. Receipt SHA553af68aabb70cfaefdb9b4ee0639cdb33a91efe2ccdf79944204ccd314d8dd1.
Subsequent unit guard corrected follow-up selection to exclude visited parents
BEFORE ranking; final full suite1477passed22skipped (84923 terminal exit0).
Frozen cohort replayv7 is the next integration/transfer check (exposed cases,
not an unseen-target evaluation).

Address-unit recovery now has solver/address_units.py, wired into both
compile_recovery (including freshly redrafted/declaration-recovered candidates)
and resilient modelrepair.normalize. It handles two distinct candidate classes:
1) entire direct global-pointer update families, requiring source-local aliases,
unmodified assignments and resolved target stores of load(global)+literal with
matching counts; changes pointer-element arithmetic to byte arithmetic;
2) explicit void** byte-view stores of linker-backed addresses, requiring an
actual target constant/symbol-address store; restores the lost void* cast.
Types remain existing candidate views, not binary-derived facts. No game-source
integration, reference-body reads, acceptance changes or model calls.

gate-address-units-original-v1.json replays the ORIGINAL frozen-v5 gate candidate.
Best attempt164/source00ee067584618045f67f772f229ebb79a2ae9f995c25d00bcb66566075a36f60:
all10 global allocator advances now8bytes and all4 pointer address casts restored
automatically. Frontend failures reduced from5 to1: incomplete-array arithmetic
at the indexed gCourseGateSoundParams call. Still noncompiling/not exact; no
semantic pass claim. The indexed gCourseGateAngles expression also still scales
incorrectly despite compiling. These two table expressions are the next gap.
Binary witnesses: signed halfword gRaceCourseIndex load then sll4 for both;
sound address is formed in the call delay slot, angle uses HI/add/LO load form.
Do not implement an instruction-order-only matcher that ignores those forms.
Five new regression tests cover wrong/unresolved/extra target stores, mutated
aliases, linker/binary disagreement and preservation of scalar/other-function
code.67focused tests and full1467passed22skipped (76915 terminal exit0).
Frozen-v6 cohort numbers remain unchanged; fresh transfer validation still due.

Automatic stack-object coalescing is now wired into modelrepair.normalize when
source_object_bounds finds a scalar byte-view extent conflict. New
solver/stack_object_repair.py requires source-bound target-compiler measurement,
a uniquely named aggregate already cast in the candidate, one fixed target
frame, actual resolved first-argument stack calls and matching binary stores.
It merges only plain spHEX locals with closed assignment-only uses and exact
measured field spelling/width; overlapping layouts, unrecognized/shadowed slots,
stale measurements, unsupported uses and ambiguous leading arrays decline.
spHEX correspondence remains a hypothesis, not binary-derived C identity.
The candidate is compiled/scored/semantically checked with normal lineage and
stack_object_hypotheses receipt; no acceptance gate or game source changed.

gate_stack_pipeline_probe.py feeds UNCHANGED saved source5c9267ba... into the
ordinary pipeline with zero model calls. Root158 -> automatic child159,
source d73f298d8f0aebefc68212c0a92b8f396f251f558c68fffdec2208a8c36f1dac,
same C tokens as assisted157. gate-stack-object-pipeline-v1.json: compiler and
frontend pass,64/64 sampled pass-with-execution-debt on SAME panelc1f993c9...,
score89.126,131positional bytes different,190target/candidate instructions.
This demonstrates activation on the motivating case, not transfer or original
source recovery: prior gate address edits remain assisted. Frozen v6 remains
unchanged, with four noncompilers; no new cohort result is claimed.
Seven focused tests include missing binary witnesses, stale/overlapping/wrong
scalar-type layouts, nonclosed/shadowed slots, untouched other functions and
zero-call controller lineage. Final full suite1462passed22skipped (13358,
terminal exit0). Immutable baseline SHA remains9f6ce843... unchanged.
Automatic probe receipt SHA256
8f48a72e07ef5c8416de182dcc1d6c18ea3d27fc4f63fb9e8de90bc2152b3768.
Next: automate the remaining gate address obligations, replay its original
candidate, then freeze/replay and test fresh functions for transfer.

Full object-bounds suite1455passed22skipped (95775 terminal exit0).

Gate stack-object diagnosis confirms a source storage defect, not merely different
stack placement. Candidate sp58 was s16(2bytes), but explicit typed accesses span
32bytes and allocFixedTransformMatrix receives it as Transform3D*. Target caller
uses stack0x58..0x74; header compiler measurement gives Transform3D owner_size32,
rotation18bytes and translation.x/y/z at20/24/28. makeFixedRotationZY itself has
nested calls (Z,Y,multiply), so it remains outside automatic leaf execution.

gate_stack_object_probe.py measures that header layout, replaces the scalar with
Transform3D, sends its rotation member to the rotation callee, and merges split
sp6C/70/74 locals into translation fields. Assisted attempt157/sourceSHA256
737fc7617493dbdbac84897d3ffc45bfa4a18136d57cb3c5479fc1d0fbf31bd5:
64/64pass-with-execution-debt on SAME panelc1f993c9..., score89.126,131positional
bytes different. This is proper-object reconstruction, not a forced stack-offset
edit. Opaque callees and sampled/object-validity limits remain; not semantic proof.
Receipt gate-stack-object-v1.json and inputs preserve measured fields/edits.

New deterministic source_object_bounds diagnostic catches scalar byte-view extent
conflicts (plain local fixed-width scalar, literal offset/type dialect only).
Real old candidate reports sp58 declared2bytes/accessed through32; reconstructed
candidate has no such scalar conflict. Reports are source-hashed, distinguish
syntax decline from no finding, and do not claim path reachability or header-type
validation. Wired into semantic reports and semantic repair prompts; no automatic
source edits or acceptance changes.29initial focused tests passed; full suite
launched after adding parse-decline regression. Next: generalize object coalescing
and the assisted gate address repairs, then original-source/frozen transfer replay.

Full partial-store suite1452passed22skipped (67712 terminal exit0).

Big-endian partial stores SWL/SWR now execute in mips_differential._store.
They touch only the selected contiguous1..4bytes, preserve untouched bytes/source
registers, and reuse stack checks, persistent write logging and interventions.
Byte ordering checked against QEMU target/mips/tcg/ldst_helper.c helper_swl/swr;
12tests enumerate4alignments for each opcode, both pair orders across boundaries,
untouched-byte/source preservation and unmapped faults. This is synthetic ordinary
memory support, not MMIO/partial-fault atomicity or full hardware equivalence.
Unaligned loads and executable-callee admission were not broadened by this patch.

gate-partial-stores-v1 attempt156 replays unchanged source5c9267ba... and identical
64case JSON from155. Panel identity changes because interpreter implementation
identity is pinned. Result20passes44failed0inconclusive: SWL obstruction removed.
First exposed difference is makeFixedRotationZY stack+0xfe0 vsstack+0xff2; angle
arguments agree, earlier persistent writes agree. Raw opaque call results/effects
depend on stack labels, so these44 comparisons are NOT44 proven source bugs.
Keep existing stack-pointee obligations and validate actual objects/callee effects;
never force stack offsets or waive mismatches merely to get passes.
New panelSHA256c1f993c9bdfd2427894d6b92bbd1737e7aeb5070bd6caa96d424c42cdc94d8a8.

Full outcome-accounting suite1440passed22skipped (90841 terminal exit0).

Semantic outcome accounting corrected and real-case verified. semantic_lane now
reports observed_failure only for explicit failed comparisons; nonempty unresolved
comparisons without failed rows are inconclusive (never accepted as pass).
outcome_accounting groups target/candidate execution reasons, counts every case,
and reports counts omitted beyond8example groups. Frozen audit/summarizer route
semantic_inconclusive separately, including old observed_failure receipts whose
counts contain only passes/inconclusive. Historical receipts are not rewritten.

gate-outcome-accounting-v1 attempt155 replays IDENTICAL source5c9267ba... and
panel782296d6... from gate-byte-address-v3.20passes44inconclusive0disagreements.
ALL44 are candidate unsupported at pc118 `swl t0,0(a0)` while target returned.
This supersedes the earlier tentative attribution to opaque matrix callees:
stack-pointee obligations exist, but the actual execution stop is missing SWL.
Next: implement/independently validate big-endian unaligned MIPS memory operations,
then replay this unchanged source/panel before attributing later failures.
6focused outcome/deferred tests passed; full-suite validation launched.

Gate address-unit controlled experiment (not yet generic integration):
gate_address_probe.py binds best150 and tests byte sound-table indexing, signed16
angle load at byte courseIndex*16, and explicit pointer casts on four linker
constants. Assembly confirms sll index,4 for both table uses and lh for the angle.
v1 target-compiles but project frontend rejects u8* versus Vec3i* formal parameter;
v2 adds the declared Vec3i* view AFTER byte arithmetic, passes both compilers.
v2 attempt153/source895e81328d4ec8f905f31622657e23538598bd1d804c8d988b8cb65bf4d304da:
9passes11failed44inconclusive. Debugger exposes region pointer advancing32bytes
instead of8 (s32* +8). v3 tests byte advances for all10 same-shaped writes:
attempt154/source5c9267baf54e7cd49aa9acfe7eb7df08936dae464e5eeb0c35ba1de7a33473ba,
score64.174,20passes0failed44inconclusive, SAME panelSHA256
782296d6bddf6223cd4a021e8f6bf3e1791be0bc38152e8ee3e11c9f2e3cace3.
Target coverage190/190instructions18/18edges does not prove semantics. Opaque
matrix callees expose differing stack object addresses;44inconclusive need their
own execution/obligation accounting. Legacy semantic status remains observed_failure
even with0failed; interpret counts, not this coarse label. No exactness claim.
Receipts gate-byte-address-v{1,2,3}.json and inputs preserve every hypothesis.
Next: extract compiler/address repairs and byte-advance rule; strengthen object/
callee evidence and distinguish inconclusive debt from actual disagreements.

Full post-declaration/direct-call suite1437passed22skipped (1867 terminal exit0).

Fresh byte-view drafts now reuse existing declaration recovery as a separately
logged candidate (raw draft retained). m2c_adapter and globals_variant recognize
both extern ? and extern M2C_UNK; scalar/mixed absolute-symbol uses still decline.
Gate-header-byteview-v5 resolves four segmented linker constants and two globals
from existing access receipts (sound slots0/4/8/12, angle signed16 at0), but remains
noncompiling. This reveals address-unit and integer/pointer conversion debt rather
than missing symbol evidence. Raw/best candidates and recovery reports retained.

compile_obligations.byte_pointer_variant now also covers direct call arguments
parameter+literal when the binary has one matching parameter-relative argument
address, the source expression is unique, and the parameter is unmodified. It does
not relax the previous assigned-local rules. Binary gate allocFixedTransformMatrix
argument is param0+0x18; the other two callsites use stack-0x20. Regression tests
cover firing, wrong offset, mutated parameter and duplicate source expressions.
Gate-header-byteview-v6 (best150/source05d58828743afdb35f788294914668bc110f74194a6ebd64707783130a8b2d6d)
automatically fixes that call and reduces frontend errors6->5: sound-parameter
array-address arithmetic plus four linker constants assigned to void* fields.
Angle scalar-address indexing may also scale incorrectly despite compiling;
do not treat clearing frontend errors as behavioral correctness.21focused tests
passed. All work remains zero-model/source-local; frozenv6 is unchanged.

Post-opaque-pointer full suite1435passed22skipped (96001 terminal exit0).

Gate opaque-pointer boundary tested: installed m2c types.py emits M2C_UNK for
unresolved types. The lowerer now accepts ONLY its ** field form as a void**
candidate view, preserving pointer-sized access with unknown pointee/extent.
M2C_UNK* scalar accesses still decline; no global placeholder typedef is invented.
Focused tests24passed. Normal original-source probe gate-header-byteview-v4
(root139,best140/source9e9e0645ace6566274840d4b5f0ab6ba9e690272a5818158d7e701620395960a)
now EMITS the byte-view candidate instead of declining. Still noncompiling:
six extern M2C_UNK globals (D_2001678,D_2001730,D_2001810,D_20018E8,
gCourseGateAngles,gCourseGateSoundParams), plus arg0+0x18 on incomplete public
struct in allocFixedTransformMatrix. Frontend lists7errors. Existing absolute
adapter recognizes extern ? but not the equivalent valid-syntax M2C_UNK spelling;
existing byte-pointer adapter handles assigned scalar-pointer locals rather than
this direct-call argument. These are next integration/representation targets,
not semantic findings. No success/transfer or frozen-cohort improvement claimed.

Frozen replayv6 TERMINAL/audited: session65006 exit0. Noncompilers5->4 through
surface byte-view+wide-return recovery;3exact preserved. Other stages1unavailable,
1disagreement,2parked,2sampled-pass-with-debt,3sampled-pass-nonexact. AuditSHA256
0388cf28560e78e5730b6d41b7c49cbe9d41e6bd2aa2634972a3e1a79e2f695b
(`failure-coverage-fixes-replay-v6-audit.json`). Baseline/game/build pins intact.

Postfreeze transfer diagnosis: drawCourseSelectCourseStats and renderCourseGateObject
reached byte-view recovery but assembly-only m2c changed public parameter types.
byteview_redraft now tries existing-header context on this ABI mismatch, then
rechecks the SAME signature lock; no interface weakening. Gate then exposed
unhandled Mtx** and void** field accesses. Lowerer now accepts explicit pointer
field forms using known header type names, plus signed literal offsets; unknown
names and unsized void* dereferences still decline.23focused tests pass.
Gate probes remain noncompiling: original-pipeline-v1 attempts133/134 declined
on Mtx**; gate-header-byteview-v2 attempts135/136 on void**; v3 attempts137/138
on M2C_UNK** at temp_v1_5+4. Those are representation boundaries, not semantic
failure or proof the function is unsolvable. No known-pointer-fields transfer
success claimed. Next: inspect m2c's opaque pointer placeholder and its actual
uses; preserve source-bound rejection evidence and compiler/runtime adjudication.
These changes are AFTER frozenv6 and require full-suite/frozen revalidation.

Full integrated-byteview suite1433passed22skipped. Frozen cohort replayv6 launched
(session65006); outcome pending, priorv5 snapshot/receipts preserved.

Original-source byte-view recovery is now WIRED and activation VERIFIED:
compile_recovery.byteview_redraft requires a locked included-header ABI and an
exact ordinary return/parameter spelling match from the fresh assembly-only
m2c draft (parameter names may differ). Unknown/conflicting ABI or changed types
decline. It expands typed-byte hypotheses through m2c_byte_view, retains include
context, emits a bounded compile candidate, and records the fresh draft/lowering/
ABI receipt. Modelrepair subsequently applies the admitted wide-return repair.

surface-original-pipeline-v1 starts with frozenv5 failing source07fe299f...
(root129 fails compiler/frontend), emits compiling byte-view130 then wide-return131.
No source preprocessing by the experiment and0modelcalls. FinalsourceSHA256
a24e579a08d070bd73a3a39d61bbaff154f7f7a2e7886a82aecad0c5efaa6547,
score63.432,838 positional differing bytes. Pipeline receiptSHA256
1b76bbeac7570a46bf7be273bd418b9013c590d94284e13a9e04d591f8bd2ba0.
Independent surface-original-geometry-v1 of THIS source passes28/28,280/280
instructions12/14edges; receiptSHA256
876f4e2fdfefccf79afab98552e1c0c5f35ff1478ee55bd0b7b183649cd8ea16.
Division opaque, synthetic object validity and two branch outcomes remain debt.
22focused recovery/byteview tests pass including ABI-change and unavailable-ABI
declines. Frozen replayv6/full-suite validation pending; no unseen-transfer claim.

Post-byteview full suite1431passed22skipped (session74144 terminal exit0).

Assembly-only typed byte-view recovery experiment succeeds without surface-specific
record definitions or primitive-pointer hints. m2c_byte_view.lower expands typed
M2C_FIELD macros inside-out; raw extern s32 address roots become byte-pointer
candidate views. Bare dereferences require a unanimous offset-zero primitive type
from directly assigned local aliases of the same root. Conflicting/missing types,
non-additive root uses, shared globals and out-of-function macros decline. These
are source reconstruction hypotheses, not newly inferred binary facts/KB types.

surface_pipeline_probe --fresh-byte-view freshly runs assembly-only m2c valid
syntax, lowers it, and sends common.h+draft to ordinary agentrepair(0calls).
It compiles/frontends and automatically receives wide-return normalization;
best127/sourceSHA256a7b91f9e9cac5a27800f893afafff63069b1e4a7256d191de06e8832411cb8e1,
score63.432,838 positional differing bytes. Default64pass covers only39/280;
independent explicit geometry attempt128 of THIS source passes28/28,280/280
instructions12/14edges. Receipt surface-byteview-geometry-v1.json SHA256
b8131950374a10c53a1a7737feca59aaef21b159f14d83cc4b655b3bb28fefbf.
Division remains opaque; fixture object validity and two edges remain unresolved.
No model calls/reference bodies/game integration.15focused byteview/adapter tests
pass. This is experiment-entry orchestration, NOT yet compile_recovery activation
from the original failing candidate. Next: public-ABI-preserving redraft integration,
normal-source activation and transfer tests. Frozenv5 remains unchanged.

Frozen replayv5 TERMINAL/audited: session57612 exit0.16outcomes, unchanged stage
counts versusv4 (3exact,5noncompilers,1disagreement,1unavailable,2parked,
2sampled-pass-with-debt,2sampled-pass-nonexact). Integrity audit SHA256
a5594d31e64c47feec22c543c11429a4fd83b650222535a5fb066d13c1c3ae94
(`failure-coverage-fixes-replay-v5-audit.json`). Wide-return change caused no
observed cohort regression; original surface layout still prevents activation.

Postfreeze representation experiment: installed m2c --valid-syntax emits
M2C_FIELD(base,type*,byte_offset), exposing different face/coordinate widths
without inventing record names. However bare integer dereferences remain; adding
primitive pointer context still leaves byte-scaled expressions that would become
incorrectly element-scaled if emitted verbatim with typed pointer declarations.
This is an unresolved representation test, not a compiling candidate. m2c_input
now exposes OPT-IN valid_syntax and structured primitive pointer_globals; no
production caller enables them. Arbitrary type C/identifier injection and duplicate
names decline, existing array-context restrictions preserved.7adapter tests pass.
Source-hashed alternatives are captured by surface_representation_probe.py;
next is controlled typed-byte lowering plus compiler/differential adjudication,
not automatic adoption of the draft. The frozenv5 snapshot remains unchanged.

Normal pipeline wide-return activation VERIFIED: surface_pipeline_probe.py invokes
agentrepair(resilient,0modelcalls) on saved compiling assisted-layout source99a1b756...
and emits one normalization child123 from root122. Child sourceSHA256
3409d3c82ae2bd385ea2a18311f1fb7a50571c2b87362203420053ed9d24b08f
is IDENTICAL to independently geometry-tested generic attempt121 (28/28 passes,
280/280 instructions12/14edges, opaque division). Child compiles/frontends,
weighted score63.343,839 positional bytes different; NOT exact. Receipt
surface-wide-pipeline-v1.json SHA256
8f2a2f3e4a269877281841592c5fa4e4c9abcdc29b9fb345cb61f1d7ef8db846.
Full suite1419passed22skipped. This verifies automatic wide-return activation
only AFTER assisted layout reconstruction. Original source remains noncompiling;
neither unseen transfer nor automatic connected fixture generation is established.

Wide-return generator extracted and wired into resilient modelrepair normalization:
`wide_return_repair.propose` requires an admitted word-pair callee, unique four-s32
prototype, plain result temporaries, exhaustive closed-use accounting, no callee
uses in other functions, and explicit big-endian target. It coordinates prototype,
union result storage, high copies/subtractions and explicit low extraction; all
other uses decline. Modelrepair obtains endian from target ELF, uses the existing
semantic panel's admitted environment, and records proposal/decline provenance.
It retains compiler/frontend/semantic adjudication and original candidates.

Independent generic-generator replay (not the modelrepair wiring) on the assisted
surface layout source: attempt121,25edits,28/28 explicit geometry passes with
280/280instructions12/14edges. SourceSHA256
3409d3c82ae2bd385ea2a18311f1fb7a50571c2b87362203420053ed9d24b08f;
receipt `surface-explicit-geometry-generic-v1.json`. Same opaque-division limits
as the assisted panel below. Focused generator/modelrepair/fixture tests28passed.
NEXT: verify normal-agentrepair activation, full suite and frozen replay. Wiring
is implemented but not yet real-pipeline validated; pointer-layout recovery still
assisted and untouched-source activation/transfer remain outstanding.

Surface-height controlled diagnosis (after frozen v4; not yet generic recovery):
`surface_layout_probe.py` reconstructs missing local record views, global pointer
slots (not inline arrays), byte-stride indexing, and a lost index initialization
from binary accesses. Attempt116 compiles/frontends, source99a1b756..., score45.825,
856 positional bytes different. Its64 sampled passes cover only39/280 instructions
and1/14 branch edges: the empty-face path. This is NOT semantic correctness.

`surface_geometry_probe.py` supplies28 explicit nonempty one-face fixtures with
both orientations/flags and seven query points. Attempt118 exposes15 failures,
13passes, covers280/280 instructions and12/14edges. Example division numerator
target0x00600000 versus candidate0x00000002 directly exposes discarded product
low words. A coordinated u64 declaration/local/high-low extraction experiment
(119) is inconclusive because IDO emits unsupported __ull_rshift. An explicit
big-endian union word-view variant (120) passes28/28 on the identical fixtures.
SourceSHA2569e60265e6207c676a0c6da0597ff8b4d7d5f771400e503a5fe4d369a2123d271;
receipt `surface-explicit-geometry-wordview-v1.json` SHA256
d0604e7042d14370fdd087177f34c011a02de6a21390b2bfea5305c0f15d5ca0.
Multiply is ROM-bound concrete execution; division has an explicit four-word
argument observation but opaque results/effects. Empty-face and repeat-loop
outcomes are missing from this panel. Fixtures are synthetic, not game-validity
proof; union layout is a target-endian hypothesis. No reference bodies/modelcalls/
game integration. Fixture test1passed; no new full-suite claim. Initial script
environment-unpacking error logged compileattempt117, corrected before118.
Remaining: generalize pointer/layout and coordinated result-width repair;
generate connected inputs automatically, strengthen division execution, and
validate untouched-source activation/transfer. Frozen cohort remains5 noncompilers;
this assisted experiment does not change its outcome counts.

Frozen replayv4 TERMINAL/audited: session66942 exit0,16outcomes/0execution errors/
0modelcalls/0integrations, unchanged baseline/game/build pins. Audit
`failure-coverage-fixes-replay-v4-audit.json` SHA256
134a7f065523fd49a9e5ebf767a0eab50b36578863af96a9584fa32e42fcac65.
Noncompilers6->5 via generic timer recovery,3exact preserved. Remaining stages:
1semantic unavailable,1disagreement,2parked,2sampled-pass-with-debt and2sampled-pass
nonexact. This is exposed-original-source activation, not unseen transfer.

Postfreeze audit hardened wide_reconstruction scope: require the named ordinary
function, use only its plain local declarations, reject volatile/global temporaries,
and do not rewrite other functions. Previously the word-declaration regex could
match a u32 token following volatile or a declaration outside the function.
Tests now cover those cases. Fresh real header redraft/measurement under the stricter
guards produces EXACTLY the previous tested candidate SHA256
3c163ba0911c930aab0cf98aa2a2fdfc5bf4feae86b525a11f29b94eab3330f2,
with all4patterns firing. Full suite1415passed22skipped. Frozenv4 kept unchanged;
the guard audit is separately tested, not falsely described as inside that freeze.
No live process remains fromv4/testing. Next: remaining5 source/type compile
failures and transfer validation; the active goal is not complete.

Generic unsigned-wide compile recovery is now WIRED, not just assisted source.
compile_recovery detects m2c's split-u64 argument marker, freshly redrafts from
assembly plus existing headers even when includes are unchanged, and measures
header layouts with the configured target compiler. wide_reconstruction.py tests
four closed source idioms: unsigned pair comparison, subtract-with-borrow,
annotated single-wide call argument, and nonzero pair copy. It requires a uniquely
mapped8-byte unsigned field, plain local pointer declaration and bounded temporary
uses; signed/ambiguous/volatile/reused-temporary shapes decline. These are candidate
hypotheses, not new KB evidence or unconditional edits. Compilation/frontends and
semantic evaluation remain responsible for acceptance; original source retained.

From ORIGINAL final timer sourcea557d324..., normal agentrepair(resilient,0calls)
emits measured-wide-operations child114/source3c163ba0..., compiles at83.915 with
249 positional differing bytes. Receipt timer-generic-wide-v1 SHA256
c7a4315b2384a8f4127d4f052d5427647a1ef67e0be0a24dc670ae277fbbfa1e.
Fresh37case controlled list replay of THIS generic candidate passes allcases,
93/93instructions15/16edges; timer-generic-circular-list-v1 SHA256
5a5746bea90e824bfce63ff157957bf030f156d8cd3dead82293344c80dc4c22.
It is not the earlier84.978 assisted source. Opaque effects/object validity and
unresolved branch proof remain; no exact or unseen-transfer claim.
Full initial suite1413passed22skipped. Replayv4 launched(session66942) to validate
all16 original sources from a new immutable snapshot/DB with0modelcalls. Pending.
After launch, a TEST-ONLY volatile-fixture assertion was corrected to compare
against its actual input; production code unchanged, frozen snapshot not edited.
Final full-suite rerun1413passed22skipped; fourth replay remains running.

Timer reconstruction path validation expanded with explicit inputs:
`timer_list_probe.py` builds a one-node circular list plus sentinel in mapped
synthetic regions, varying64-bit value/interval halves, elapsed20, message-null
versus present, and empty-list state.37cases all pass target-vs-compiled-candidate;
93/93 modeled instructions and15/16edges covered. This exercises the previously
unvisited removal/message/interval-copy/call paths and borrow across the low word.
timer-circular-list-v1 attempt112/source2a9a4440... SHA256
71cece34d456f46e2683ff7e226e45285104d5c00bebf920dff24f85cf45ae51.
One remaining edge (i34 taken) comes from unsigned highword<0 and appears infeasible;
no mechanical infeasibility certificate exists, so the report leaves it unresolved.
osGetCount is explicitly fixed to20; queue/insert callees remain opaque. This tests
source reconstruction under an assumed environment, not actual timer scheduling
or game object validity. Candidate byte distance is unchanged; no integration.
Fixture regression verifies37 unique cases, sentinel links, compare/borrow boundary
values, high/low interval arms and both message states. It does not infer layouts.
Next is still generic source-bound wide-operation reconstruction from the original
noncompiling candidate; this controlled panel is not an automatic repair generator.

Timer compile cause reproduced with an assisted coordinated reconstruction:
`timer_wide_probe.py` binds the saved final source and tests two explicit spans
derived from binary word operations/header declarations (no reference C bodies).
Offset0x10/0x14 accesses are value high/low, not interval/value low; the draft's
whole-field narrowing and separate borrow store are inconsistent. The paired
unsigned compare/subtract becomes value>elapsed/value-=elapsed, the0x8/0xC
nonzero/copy becomes interval!=0/value=interval, and __osSetTimerIntr receives
one OSTime argument rather than two independently passed words.

timer-wide-reconstruction-v1 attempt111/source2a9a4440... now compiles and passes
the project frontend, score84.978,94instructions (same count as target),160
positional text bytes different. ReceiptSHA256
b43e21a727cd734d6fbf9720c3f441fbe83480b2ad6d053264d49045d3dc4a5e.
64sampled passes cover55/93 modeled instructions and7/16edges; interval reinsertion
and linked-list removal paths remain unvisited. Not semantic proof or byte exact.
This is a TARGET-SPECIFIC diagnostic reconstruction, NOT a wired generic repair;
the frozen cohort still has6 noncompiling candidates. Next: extract source-bound
wide-field/word-pair operation evidence into the repair machinery and validate its
activation from the original source, with valid linked-list path tests. No game
integration, historical receipt replacement or new autonomous solve claimed.

Frozen replayv3 is TERMINAL and source/DB/certificate/pin audited. Session93330
exited0. All16 outcomes,0execution errors,0modelcalls,0integrations; immutable
baseline and game/build pins unchanged. Audit
`eval/results/failure-coverage-fixes-replay-v3-audit.json` SHA256
e96fa73b3f02f5ce28d8d4660b8bbd170ce15bd25f01ff6a67bb81ec07317045.
Stages remain3exact,6noncompile,1disagreement,1semantic unavailable,2parked,
2sampled-pass-with-debt,1sampled-pass-nonexact. Course selection reproduces50/50
instructions after nested-input repair in the frozen worker. No new exactness
claim versusv2; this is integration validation on exposed development sources.

Auditor now verifies that all-trial status counts sum to attempted_cases and
retains coverage/admission/trial evidence alongside source-bound champions.
Absent historical counts are explicitly not_recorded, never reconstructed.
Observed trials: DevMgr475/475memory faults; PFS310returned/140faults; progress
3667returned/999faults/334unsupported; audio261returned/4108faults/43step-limit/
588unsupported; course selection75returned/228faults; osDestroyThread817returned/
2111faults/8step-limit. These are target exploration outcomes, NOT candidate
correctness counts, and do not prove object validity or universal reachability.
Current Panel semantic feedback now retains exploration_trials as well (format
wiring added afterv3freeze; full1409passed22skipped, targeted32passed).
Next substantive work: six noncompiling source/type failures, starting with
coordinated split64-bit field and call reconstruction for __osTimerInterrupt.
No remaining live campaign/test process from this validation turn.

Concrete-callee coverage regression diagnosed and repaired: replaying the prior
course-selection panel with the real callback setter returned36cases and faulted28
on invalid gCurrentGameTask pointees. concrete-callee-path-probe-v1 records the
nested fault and the precise inconsistency: _failure_input_location identifies
gCurrentGameTask/4, but _read_locations omitted it because only caller traces were
examined. _execute_callee now saves full child input_read_locations BEFORE trace
serialization/truncation. _read_locations unions those mutation inputs without
merging child instruction IDs into caller coverage or def-use graphs.

Real unchanged-source replay nested-input-caller-v1 attempt110/sourcea3c99d28...
restores50/50 modeled instructions and6/6 branch edges,64/64 sampled passes and
the existing object-exact certificate. ReceiptSHA256
0b92a781a45a490635d967f7ee951ec79ac50829e62d268cfd7dc7e5969b39b9.
This repairs synthetic input generation, not proof of valid game object graphs
or universal semantics. Regression fixture asserts fault -> pointer mutation ->
completed caller coverage, preserving child/caller instruction namespaces.

CoverageExploration now also counts EVERY trial status and keeps at most8
noncompleted examples, including bounded nested fault/read metadata. Panel reports
exploration_trials separately from retained target_execution/semantic samples;
discarded failures cannot be inferred from the selected panel alone. Counts must
sum to attempted_cases. Final all-trial accounting suite1408passed22skipped;
frozen16-function replayv3 launched (session93330).
Replayv3 uses a new frozen snapshot and baseline copy,0modelcalls/nointegration;
results pending, do not count as completed validation yet.

Linked admission padding/alignment audit (September6): from_extracted accepts at
most3 explicitly annotated contiguous zero NOPs after endlabel, verifies their
ROM mapping/bytes and records verified_excluded_padding_bytes; these words are
NOT appended to the executed function. Unsupported opcodes are rejected before
linking so nested jal no longer masquerades as an undefined linker symbol.
Recorded8-caller admission replay linked-padding-admission-v1 exposed the next
boundaries: lwl, relocation-addend syntax, MMIO symbols, nested calls/unknown ABI.
Padding alone admitted no additional callees.

A separate reproduced binder bug affected non16-aligned function starts: GAS's
default section alignment caused ld to insert leading bytes. Temporary parsed
object .text alignment is now explicitly4 before linking; instruction bytes and
ROM checks remain unchanged. A synthetic0x80001004 failing test now passes.
linked-padding-admission-v2 SHA256
bdc9f8747955fdab7cb6e95e0c2dcc80135b777647dd6db7729309ec5f6672ca
now admits setCurrentGameTaskCallback at0x8009956c.13 WSL assembler tests pass;
full Windows suite1407passed22skipped.

IMPORTANT runtime follow-up: linked-alignment-caller-v1 attempt109/sourcea3c99d28...
still object-exact/64sampled passes, but coverage drops from50/50 to11/50 caller
instructions (1/6edges), and admitted callee sites are UNVISITED. ReceiptSHA256
72d669329fec28472840f28253c6c6baf3f87208de19719e105c43856c6ca51c.
This is not proof of useful callee runtime coverage. Five retained target runs
return; the completed-panel target_execution report does not establish what
happened to all rejected exploration trials. Next: inspect concrete-callee path
discovery/input-object repair and retain causal evidence for discarded trials.
Historical frozen cohorts/pins untouched; these are development admission/runtime
probes, not a new frozen whole-cohort validation or game integration.

Default linked-callee execution activated and replayed (September6):
semantic_lane.Panel passes independently recovered call contracts to
callee_execution.load_binary_leaves. After basic leaf admission declines, a
supported project-header integer/void ABI can enable linked_callee.from_extracted.
The fallback needs uniquely sized binary assembly, contiguous PC/ROM annotations,
closed inline local word tables and matching symbol metadata. It validates whole
ROM identity, reassembles interpreted instructions at their linked address and
checks every initialized table byte. No prepared callee workspace, reference C,
manual callee-name list, inferred effect summary or candidate ABI authority.
The same512-instruction/no-nested-call/integer policy remains. All declines stay
visible in callee_admission, including linked_decline. Unsupported trailing
assembly padding and relocation addend syntax are remaining loader limitations;
calls, hardware, general FP, object extents and callback effects remain separate.

Frozen16-function replay v2 finished0errors/0modelcalls/0integrations, preserving
all previous snapshots and baseline. Source-/DB-/certificate-/pin-bound audit:
`eval/results/failure-coverage-fixes-replay-v2-audit.json`, SHA256
228911bd8fc7b51e41fa3dbcf37db5a45b8daaa83e1000f58f7dc7145a2411c8.
Counts:3object-exact,6noncompile,1disagreement,1semantic unavailable,2parked,
2sampled-pass-with-debt and1sampled-pass-nonexact. Progress-meter now SELECTS
source13add2f8... at94.444/175 differing text bytes with64/64 sampled passes,
instead of leaving it only as byte champion. Automatic ranking-callee admission
verifies1276textbytes/36tablebytes. Coverage85/89 modeled instructions,10/14edges;
opaque addRenderCallback, synthetic object validity and unvisited traps remain.
Audio still uses the weaker default callback regime, not explicit ABI/effect
admission. No new exact functions versus replayv1 and no universal semantic proof.
Tests:1405passed21skipped full suite;10 real-assembler linked-callee tests passed
under WSL; final focused16passed2skipped after documentation-only docstring edits.
Replay harness accepts --version to create new immutable experiments; audit accepts
--receipt/--out. Next work: loader padding/addend declines and split64-bit timer
field/call recovery; the six noncompile causes still need controlled repairs.

Frozen default-fix activation replay completed, September6:
`eval/experiments/campaign-gap-audit/replay_fresh_fixes_v1.py` freezes code,
copies the immutable zero-attempt ROM baseline, pins original final sources and
build inputs, refuses heldouts/overwrites and disables model access. It runs
agentrepair(resilient=True, deterministic_budget8/depth2, semantic64/10000)
on14 saved final candidates; the2 parked nodes receive fresh raw-ROM instruction
classification. All16 finished without execution errors; no game integration.
This replays exposed development failures, not unseen-target transfer.

Source-/DB-/certificate-bound audit with BOTH byte and semantic champions:
`eval/results/failure-coverage-fixes-replay-v1-audit-v2.json`, SHA256
7ba7bded0da3a9f570e10585125b5dd9c2c90c0da290324447b48a159bb413d8.
Outcomes:3 object-exact,6 noncompiling,2 differential disagreements,
1 semantic unavailable,2 parked,1 sampled pass with debt,1 sampled pass/nonexact.
Course-selection final source now reaches exact in5 deterministic candidates;
the2 historical exact matches reproduce. Audio reaches64/64 sampled passes with
explicit unknown-callback debt, not the stronger opt-in callback contract.
Progress-meter generic repairs reach retained byte champion94.444/source13add2f8...
in6 candidates, but selected source stays71.528 because default opaque ranking
callee effects corrupt its semantic ranking. The explicit validated-callee paired
experiment remains separate; automatic environment admission is the next activation
gap, not absence of a source repair. Never collapse champion scores into one.
Auditor `audit_fixes_replay_v1.py` reuses campaign symptom ownership, preserves
causal_accounting_complete=False and supports new --out receipts without replacing
history.8 harness/audit tests pass; full suite1398passed,20skipped. Frozen snapshot
and immutable baseline unchanged. Next: admitted callee environment wiring plus
the6 unresolved compile candidates; no claim all observed causes are closed.

Explicit callback argument admission implemented: callback_abi.admit freshly
binds a supplied measured-header packet to an exact parsed program identity
(instructions, labels, data, symbols, code base). Environment.callbacks retains
separate program descriptors; Runner uses only matching instruction indices.
Changed programs do not inherit old contracts. jalr reads declared o32 words
after its delay slot; CallEvent retains the ABI/path contract. Synthetic
callbacks remain opaque: no callback body/effect/stack-pointee admission.
Duplicate program descriptors are rejected. Model feedback now includes bound
callback contracts alongside raw stack-pointee obligations.

audio-callback-runtime-v1 runs one shared panel with fresh header measurement
and independently bound target/baseline/edited programs. Attempts107/108:
both32/64 pass; the stronger comparison exposes the old unobserved callback
arguments. Assisted one-element Acmd advance source7df734f1... makes declared
argument4 agree (player+8 instead of player+0x40). Argument1 still has differing
stack labels; synthetic return hashes propagate that disagreement. This is not
a verified source failure for stack placement and must not trigger forced offsets.
ReceiptSHA2564b9ec564556cc6df007c55f848c52eb39e7bc43bed75f21432141f8d253e2e8d.
Default automatic callback loading remains OFF until paired binding/object-effect
controls are integrated. Next: explicit object/pointee contracts and richer
first-call argument progress, preserving environment uncertainty, then frozen
unattended activation replay. Full runtime-change suite1390passed,20skipped.

OSS indirect-evidence probe is now terminal: audio-frame-indirect-oss-v1,
attempt106/sourcec31b9206...,1 logical call,3 transport attempts each240seconds,
all TimeoutError;0 generations/children. No evidence that OSS rejected or could
not understand the callback diagnosis. ReceiptSHA256
ffbea669008b47b659d988892528d1a6d5e76ea873e36a2ac06bc6ea26f49a36.
The experiment stopped with no evaluated child; max_calls2 was a cap, not an
observed2 calls. Session82660 exited0 and is no longer running.
Final callback/CFG regression suite:1389 passed,20 skipped.

Normalized CFG address handling is now shared rather than confined to the
private callsite-contract adapter. workspace.semantic_assembly marks its
function-relative normalized dumps with MIPS_DIFF_NUMERIC_BRANCH_BASE0x0;
cfg.build resolves hex byte destinations only under that explicit convention,
retaining alignment/range checks, symbol priority and unknown external targets.
callsite_contracts' old private label reconstruction delegates to this path
and preserves annotations. Unmarked/raw assembly behavior is unchanged.
Real audio static analysis went from2 reachable blocks/0calls to23blocks/4calls.
Recovered callback paths match alGlobals -> load+0x38 -> slot+0x8/+0x4.

callback_abi.bind is a new diagnostic-only consumer of binary pointer provenance
and target-compiler-measured header layouts. type_constraints.measure now records
top-level header global declarations from the active header AST. The binder
requires an unambiguous global pointer root, unique measured4-byte fields and a
closed fixed-word ABI; it declines lost roots, ambiguous union fields, variadics,
unprototyped/float/wide ABIs and records reasons. It does NOT execute callbacks,
admit effects, modify KB evidence or infer object validity.
audio-callback-layout-bindings-v2 verifies setParam3words at targeti127 and
handler5words at i135; player callbacki63 remains unresolved (lost stack-root
identity). Header rootALGlobals*, drvr.outputFilter offset0x38, ALFilter handler
offset4/setParam offset8 are independently compiler-measured, not copied from C
implementation bodies. ReceiptSHA256
3692d2db1b75b9da2190a936d7f27b86db2c2e48f119306cc1d098b8b3174f4a.
Next: source-/program-bound target AND candidate runtime arity admission, keeping
stack-pointee/effect debt; arity alone cannot validate callback behavior.
Full regression1389 passed,20 skipped before final decline-message refinement;
targeted binder3 passed afterward. OSS session82660 remains live/pending. That is
an unfrozen development probe, not a clean frozen-code validation claim.

Audio pointer-difference repair now wired into rewrites.propose:
pointer_difference_scale_rewrites requires same-typed non-byte pointer operands,
an explicit source right shift and a surplus candidate arithmetic shift of that
amount. It tests removing the extra scale; source correspondence and common
object validity remain hypotheses, not compiler facts. Source-bound replay
audio-frame-pointer-difference-v1 root95 ->104,8 deterministic candidates,0LLM:
32/64 ->64/64 sampled passes, score67.687 ->68.289, still505 positional bytes
different. Only selected source change removes >>3 from (var_s4-cmdList).
ReceiptSHA256 afc95343f5296cc4a4587a4bda2119ff9b37db9890d9a9cc2eac0205de3b386f.

Indirect callback coverage hooks now mark CallEvent.arity_known=False, NOT zero
arity. They retain8 unclassified o32 ABI-word observations after the delay slot
and before synthetic clobbers; unavailable stack words do not fail execution.
These observations NEVER enter comparison, return hashes or correctness gates.
semantic_lane emits bounded indirect_call_obligations, marks sampled passes
with execution debt even when observed ABI words agree, and modelrepair forwards
the diagnostic packet. Campaign audit routes these obligations to callee_execution.
No automatic callback ABI/effect admission is added by this diagnostic machinery.
audio-frame-indirect-window-v1 attempt105/sourcec31b9206... retains64/64 passes
WITH debt. Callback1 exposes word4 target player+0x8/candidate player+0x40 and
word1 differing stack labels. Do not force stack offsets; header-only inspection
finds ALCmdHandler(void*,s16*,s32,s32,Acmd*) in synthInternals.h, ALFilter.handler
and setParam fields. A validated callback-field/offset/arity binding is still
needed before those snapshots can become compared arguments.
ReceiptSHA256 0bc757e04a91f3adedc477eb9afc876f9625e59676c995c73f0fbde1e79aff1f.
Full suite1383 passed,20 skipped. Tests cover unknown versus known-zero arity,
delay-slot capture, ignored-but-exposed register/stack differences, bounded
prompt retention and audit routing. Local OSS2-call experiment
audio-frame-indirect-oss-v1 launched(session82660); result pending.

Fresh second cohort is terminal at its work budget (launcher25245 exit0), not
complete decompilation. fresh-batch-2-final-audit-v1 records8 source-bound nodes,
27 logical model calls:4 noncompiling,1 parked,1 semantic unavailable,
1 disagreement,1 sampled pass/nonexact,0 exact and0 integrations. Together with
batch1 this satisfies cohort execution, not causal accounting or fix validation.

COP1 paired doubleword transfers added to mips_differential: ldc1/sdc1 preserve
raw64-bit memory bits, require even f0..f30 and8-byte alignment, retain both
store inputs/provenance and normal memory bounds/intervention behavior.
Independent big-endian MIPS-I l.d/s.d macro expansion exposed a PREEXISTING
register-order bug: FR=0 even register holds low32 bits, odd holds high32 bits.
Corrected double conversion/arithmetic and dag_pipeline_pilot input seeding
together. tests/test_doubleword_cop1_memory.py checks assembler word encodings,
raw-bit memory layout, odd/unaligned refusal and arithmetic from literal IEEE
memory fixtures. Existing double tests corrected rather than used as authority.
Historical double-sensitive sampled semantic receipts require corrected-runner
replay; no historical receipt is rewritten and byte certificates are unaffected.
audio-frame-cop1-double-v1 used the WRONG pair convention; preserved but invalid
as semantic validation. Corrected v2,attempt94/source64d8cc9c..., receiptSHA256
960de4828520b0570199f74cb2541ef06fa85274ac1f75f094675ac27030e70b:
32/64 sampled passes, versus no completed cases before ldc1/sdc1 support.
Coverage111/166 modeled instructions,13/24 branch edges. First concrete delta:
candidate (var_s4-cmdList)>>3 scales the typed Acmd pointer difference twice;
target has one sra3, candidate two. This diagnosis is not yet a repaired source.
Remaining: invalid initial alGlobals object graphs, indirect callback ABI/effects,
opaque _collectPVoices, unvisited cvt.d.s/cvt.s.d path and FP exception behavior.
Full modeled semantics are not established; no reference bodies/integration.
Post-change full suite:1376 passed,20 skipped; WSL COP1 suite including real
assembler control:12 passed. Final batch2 audit SHA256
74e8b5acf065e07bbde63d0655eb18399a7f17984ba79daf822197c85d143443.

Course-selection width repairs now wired into solver.rewrites.propose:
pointer_element_width_rewrites requires paired same-operand lw/narrow-load and
index-scale residuals, then tests individual source-local indexed extern
pointees (s32/u32 -> s16/u16 or supported byte views). Declaration attribution
is a hypothesis, not evidence; headers are untouched. global_load_signedness_rewrites
uses an identical-operand named relocation load pair to retype a matching
source-local scalar extern. Both actuators are bound to the original source.
course-selection-pointee-width-v1 repaired the table stride/load:42/64 ->64/64
sampled passes, one byte remaining. v2 composes both generic edits from the
same original source77e29bbc...: root85 ->91, five deterministic candidates,
zero model calls, object-section certificate exact,208/208 text bytes equal
and relocation expressions equal. ReceiptSHA256
d9abf343168763a0ea9f7b10ea11569012503218a85cfe1d5410381bfc8049b2.
This is development replay of a recorded batch2 failure, NOT a changed frozen
campaign outcome, unseen-function transfer, whole-ROM verification or integration.
Tests: tests/test_pointer_element_width.py covers activation, composition wiring,
source binding and refusal on missing/misaligned evidence or header-only types.
Fresh batch2 remains active; final stage audit correctly refuses its inflight item.
Post-change full regression suite:1365 passed,19 skipped. Saved best-source hash
matches certificate candidate_source_sha256; certificate source_sha256 separately
identifies the compiler input. No game-source integration performed.

Opt-in real linked-callee execution validated: linked_callee.admit performs
fresh ROM binding, restores verified trailing NOPs, assigns real code base,
and admits bounded integer instructions/local resolved table jumps. Nested
calls and arbitrary computed jumps are rejected. Present break instructions
remain deferred unsupported traps if reached; no exception emulation.
Environment manifest records linked ROM binding/backend/base/result registers.
progress_output_probe --real-callee runs identical saved sources63/66 using
the real ranking callee on ONE shared panel. Replay attempts78/79:
unsigned source40/64 pass, signed source64/64 pass WITH execution debt.
Receipt progress-output-real-callee-v1.json SHA256
3ad3667cc27bee130e9791697b7fb367cf16ffc9b89a4ec6388154e5b71dcfe0.
Remaining debt: synthetic input validity, opaque addRenderCallback, unsupported
exploration paths and bounded coverage. Target exploration covers85/89 modeled
instructions,10/14 edges; not universal semantics or byte-exact. Default callee
loader still only admits its prior leaf dialect; new linked path is explicit.
Real-toolchain ROM/admission/trap/nested-call tests:2 passed in WSL environment.

Linked-callee ROM binding module added (solver/linked_callee.py), NOT automatic
execution admission. It reassembles the interpreter's parsed instructions,
links observed symbols at metadata addresses, compares the caller-supplied
audited full function extent against SHA1-verified ROM, and verifies referenced
initialized bytes/jump-table targets. Tool/config/source hashes and the extent
authority are recorded. Missing normalized trailing words may only be NOPs
that match the corresponding ROM words; arbitrary omitted code cannot pass.
ranking-callee-rom-binding-v1 verifies1276 text bytes and36 table bytes:
318 parsed instructions plus1 verified trailing NOP. Toolchain-backed regression
accepts an exact fixture and rejects changed instruction/table/symbol address.
WSL project pytest:1 passed (system python lacks pytest); production Leaf guard
still unchanged. Next: an explicit admitted linked-callee descriptor using this
binding, real code base and completed delay-slot program, then paired replay.

Explicit per-program code bases implemented: Program.text_base defaults to the
historical base and is checked for alignment/32-bit bounds. Jump-table seeding,
call link addresses and indirect jump resolution now use the program's base.
Dependency jump-table code ranges must not overlap caller/other dependency
ranges. Unaligned jr targets now decline instead of rounding to an instruction.
Environment manifests and concrete-call records retain the callee code base.
Synthetic real-call regression follows a table at a distinct0x90000000 callee
base and returns42 to the caller; foreign, unaligned, overlapping and invalid
range cases decline. Production linked-callee admission is still unchanged.
Next step is verified ROM/object text-and-data loading with explicit addresses;
this machinery alone is not a certificate or real ranking-callee integration.

Exact final-source replays now recorded for fresh batch1:
devmgr-final-source-replay-v1 root69 matches final261 source04ff441c...;
its retained root semantic record has5 target memory faults after opaque
osRecvMesg (null message pointer read at i14). Normalized child70 is separately
recorded; neither reconstructs the stub body. ReceiptSHA256
8e76d79cd8152d9b8bd414c4cf97453c1b19ecf491b586fc0605b2223d117d01.
course-gate-final-source-replay-v1 root71 matches final259 sourcec825feb6...;
absolute/global/alias children72-74 activate current recovery. Still noncompiling
on opaque layout/named-field correspondence. Best73 lacks the alias; child74
with alias is retained in frontier. Activation is not best-selection or success.
ReceiptSHA256 3816d6d0e3db12909fd70a2738135b77ca422777930bdd9d188535a0daaaeb64.
Both used0 model calls, no integration, and do not alter frozen campaign results.

First fresh cohort terminated at40 work items; second batch started in the
same launcher (handle25245). Source-bound fresh-batch-1-final-audit-v1.json:
8 functions/33 logical model calls,2 accepted object-exact,2 noncompiling,
2 differential disagreements,1 semantic unavailable,1 hardware parked;0
integrations. Exact: osSpTaskYielded attempt3 and
updateRaceUiTrickPrizePayoutRevealMoneyRow attempt72. Not paired acceptance yet.
Audit initially rejected DevMgr source identity because Windows locale-default
decoding changed valid UTF-8 source before hashing. Raw artifact UTF-8 bytes,
DB source and expected hash agreed; summarize now explicitly reads UTF-8 and
a locale-simulation regression catches the former bug. Historical files were
not altered. The final DevMgr candidate261 differs from probe58 (dummy-frame
addition), so its source-specific diagnosis still needs a final-source replay.

Dependency-context preparation implemented in mips_differential.execute_case
and compare_programs. Both now union admitted leaf symbols, linker addresses
and extents with caller context and seed dependency initialized data before
explicit test inputs. Conflicting linker identities, physical initialized byte
conflicts (including aliases), and out-of-range callee jump-table offsets fail
closed. Existing Leaf admission is unchanged: real relocation-bearing callees
are NOT yet automatically admitted. Synthetic real-call regression exercises
a callee-only global through both entry paths and obtains the expected value.
Jump-table bytes use the current per-program virtual text-address convention;
cross-program code-pointer escape/identity and independent ROM text/data binding
remain required before production admission. Counterfactual intervention path
still excludes concrete callees. This is context groundwork, not a claim that
the real ranking callee is integrated or the progress meter solved.

Ranking callee isolation refines the previous backend diagnosis:
ranking-callee-isolated-v1.json (reproducer ranking_callee_probe.py) runs the
existing normalized object through the existing interpreter for course0..8,
player0 and fixed synthetic inputs. All9 return (30-52 executed instructions).
Parsed artifact:318 instructions,3 symbols,9 jump-table data words. This is
artifact-bound capability evidence, not a fresh independent ROM certificate or
full-path equivalence proof. The interpreter already supports these tested
global/jump-table paths; the gap is concrete-callee admission plus shared symbol,
initialized-data and address-identity preparation at the call boundary.
Required next step: preserve ROM/text/data binding while merging dependency
symbols/data for both target and candidate; retain conflicts as hard errors.
Do not merely remove Leaf's relocation guard: execute_case/run_differential
currently construct memory from caller programs, not dependency programs.

Controlled progress-meter callee experiment completed:
progress_output_probe compares fixed source63 and66 on the SAME target-led
panel within each of three explicitly synthetic output scenarios (0,128,-1;
second output0). It uses executable synthetic assembly, not the real callee.
For output128, unsigned parent passes61/64, signed byte champion64/64; both
pass64/64 in the other scenarios, all with execution debt. Source/panel hashes
were checked. Replay attempts67/68; receipt progress-output-controlled-v1.json
SHA256 aacc6236242ee0774a6db3a263737e1489489db0040fc6b92a5476c218f90c1e.
This supports the division hypothesis under controlled outputs and shows why
opaque-stack ranking was inconclusive. It does not model actual ranking logic.
The actual callee target.s contains global relocations and a computed jump table,
outside the currently admitted relocation-free leaf backend. Need shared-global,
jump-table-aware callee execution or a separately validated effect contract;
synthetic leaf stays experiment-only, with no default registration/integration.

Signedness repair activation now includes matched-register div/divu residuals,
reusing the existing unsigned declaration/cast/literal actuator. Comparison-only
activation previously left this source cast inaccessible. Regression verifies
matching operands and rejects reverse/unrelated register pairs.
progress-meter-signed-division-v1 (zero model calls) produced attempt66/source
13add2f803ce65016aee1dd453959b10b3b277d43778d400c928bfa9fb4a9bcd,
94.444 weighted versus83.124 parent; retained as byte champion. Overall best
remains parent63 because the diagnostic semantic key favors it. Both fail64/64
on the same90f5baca... panel with unmodeled output buffers; this cannot prove
the signed repair semantically worse. Preserve both candidates, fix/test the
callee effect model before using that feedback to judge this source hypothesis.
ReceiptSHA256 4a9d13eb910b670a8399cb2f2ec6f90e4674ecfaf5c7ef6b925925d2992c18d7.
Full suite1354 passed/18 skipped. No integration or exact/semantic promotion.

Target-diff-guided byte pointer steps now participate in rewrites.propose:
byte_pointer_step_rewrites matches a target self-addiu byte stride (>=16,
non-SP/GP/RA) with a typed local `p += literal` and proposes explicit byte
arithmetic. This is a correspondence hypothesis; compiler/semantic gates remain.
It declines scalar/byte pointers, mismatched literals and masked comments.
Zero-model progress-meter-byte-stride-v1 activated this generator: root59 to
best62, source4880deefbe2612dfc925188b8cc0a69f64d759b7fc4ca03b8f89f11577f7ba23,
71.528->83.124 weighted,259->193 positional differences. Still64/64 disagreements;
no semantic or exactness claim. Remaining signed/unsigned division and opaque
getRacePlayerRankingProgress output buffers require independent diagnosis.
ReceiptSHA256 75e64aef08d54ade0dc2e66e1539a4cc82e2ffa4f772f2d5c6a9c5bb6e10822d.
Full suite1353 passed/18 skipped. Frozen fresh campaign remains unchanged/live.

Unavailable semantic panels now retain target execution status counts, up to8
distinct noncompletion examples with inputs/errors/instruction counts, target
coverage, callee admission/contracts/environment, and execution obstructions.
Previously the no-completed-cases return discarded this evidence. Campaign audit
routes the retained failure evidence without declaring a candidate bug or
nontermination. A real-runner division-by-zero regression covers the empty-panel
path; unavailable remains non-authoritative and is never a semantic pass.
Live devmgr-empty-panel-evidence-v1, attempt58, unchanged source89bd9603...:
5/5 target cases memory_fault at i14 (lw t9,0x14(t8)), message pointer zero
loaded after opaque osRecvMesg. Queue/message output effects are unmodeled.
Candidate is an empty stub,3 instructions/16 bytes versus292/1168 target;
0.636 weighted score is not meaningful behavioral progress. Receipt SHA256:
e8712df97194c4a24d29908fabb4d1b5eabf2b9fd9f0d63e887190c79d40c5b9.
No model calls/integration. Frozen campaign unchanged and still live.

Opaque tag spelling recovery (current worktree, not frozen fresh snapshot):
compile_obligations independently supplies a missing `typedef struct Tag Tag;`
for an unqualified pointer parameter backed by an included forward tag. This
does not complete the object layout. Private planning removes that alias when
necessary so a later clarified candidate can still receive layout completion;
regressions cover separate/combined repair, idempotence, and subsequent recovery.
Ambiguous-layout declines now include source member names and observed parameter
offset/width slots, explicitly without claiming name-to-offset correspondence.
Zero-model course-gate-tag-recovery-v1 attempt57 (source3190f16bec7c834d40c7bf63e64ee1a8e0559fb0dd0550d4e80da54a3c0266be)
removes missing-tag diagnostics but remains incomplete-layout/noncompiling.
Receipt SHA256:981345e5ced3fae042019c32d8d59713f3d7ce30f06c00b54bf415b3201d6253.
Full suite1350 passed/18 skipped. No game integration or semantic claim.

Absolute linker-symbol recovery now runs inside compile_recovery. Previously it
was available in initial m2c adaptation but later contextual/model drafts could
retain or reintroduce `extern ?` address symbols. The existing resolver now masks
comments/strings and declines mixed value/address or binary-AND uses before
removing a declaration. It does not infer object types from linker addresses.
Tests: test_m2c_adapter + test_compile_recovery; full suite1348 passed/18 skipped.
Live zero-model `course-gate-absolute-recovery-v1.json` resolved all four
D_2001678/D_2001730/D_2001810/D_20018E8 declarations. Attempt54 remains noncompiling:
CourseGateObjectEffect is only a forward-declared struct, and opaque completion
declines ambiguous field-name mapping. Thus this is activated syntax recovery,
not a solved function. No model calls, no integration; frozen fresh snapshot
unchanged. Receipt SHA256:
b70601b2e2b061fcdbf10b906760461038044256ede5b8d4c64ad5814b2a0b68.

Current continuation audit (supersedes live/pending HUD notes below):
`hud-callee-abi-debt-v1.json` completed, attempt46, unchanged assisted source
f1ba66f06362041ea02cf302a97c791bf25f3b646a6352686de9d076f81317ba.
64/64 diagnostic passes WITH execution debt; all223 target instructions and
21/22 conditional edges covered. sprintf remains unknown-arity/opaque, with
unmodeled stack output contents; this is not semantic equivalence certification.
Object remains nonexact:727 positional byte differences,896/928 text bytes.
Receipt SHA256:69dea17b76c7390212809e41cb20ba1368847ad52ea0114d62b9239af6bf0016.

Fresh selection independently reconstructed from immutable baseline and frozen
preflight exclusions:16 distinct functions,8 per batch,8 eligible strata among
1396 eligible functions. Baseline/inventory hashes, deterministic selection,
name/address exclusions and frozen heldout guards passed. No missing selection
within those8 strata; absent strata are not evidence of general coverage.
Selection SHA256:cd2d23ecc374446f3519c32d802badff9c755ae20abad8594fa0c6a88f508092.
Preflight SHA256:bddb7becf602f06999815ed77a43dd309924aba7f29029e63493e38a78a689e5.
Batch1 remains live: osSpTaskYielded and
updateRaceUiTrickPrizePayoutRevealMoneyRow reached object_exact;
osMapTLBRdb parked. These are interim results, not completed paired validation.

New stratified run launcher active (handle25245): fresh_run_v1 copies current
solver/eval/kb/patterns/tools/miner/tests into failure-coverage-fresh-code-v1,
and the verified immutable ROM DB into kb-sbk1-fresh-cohorts-v1.sqlite.
Preflight binds528 exposed names/186 known selection receipts,zero attempts and
inferences,2113 functions/72041 evidence rows. Two cohorts will be selected before
execution,3 logical model calls/visit,40 work items/batch,timeout240,6000 tokens.
Selection receipt is now written and batch1 intake is active; not yet fresh-validation success.
Frozen code hashing now includes miner; regression detects its modification.
Full suite1346 passed/18 skipped. HUD handle89558 remains live separately.

Raw-only semantic prompt replay produced one compiling OSS child but regressed
63/64 to47/64 cases; root retained (`pfs-release-oss-raw-source-v1`,attempts51/52).
Two other proposals exceeded hypothesis metadata length. Resilient recorded
search now uses existing truncate_hypothesis mode: full raw response remains
recorded, only the short hypothesis field is bounded; edit gates unchanged.
No truncation without a recording connection. Worker regression verifies this.
Automatic byte-view diagnosis/repair and fresh cohorts remain outstanding.

Semantic prompt fixes: condition-operand edits are explicitly allowed (previous
assignment-only wording conflicted with the PFS guard repair); strategy_brief
now survives semantic/type prompt replacement. Regression checks actual accepted
condition edits. Live condition-prompt v1 still failed: OSS copied display labels
L56/L57 into old/new spans, then malformed its correction. Plain old/new semantic
mode now shows only raw C; line slots remain for coordinated slot mode. This
presentation change is unit-tested but its new OSS replay is still pending.

OSS control-feedback results: high-reasoning v1 completed2 requests but exhausted
6000+4096 output tokens with repetitive reasoning/no finalized edit. Low v1
returned203 tokens proposing a wrong-bank hypothesis and a duplicated old span;
strict edit application rejected it. Neither changed the source or63/64 outcome.
Existing retry-invalid mode is now under test (`pfs-release-oss-control-retry-v1`).
These results do not show that more time alone or branch history alone is enough.
HUD diagnostic exploration remains CPU-active, handle89558/PID50857.

Frozen ROM paired campaign has terminated at its budgets. Final audit:
`failure-coverage-rom-paired-final-audit-v1.json`:19 functions,58 logical model
calls,4 object-exact,7 noncompiling,3 parked,2 differential disagreements,
1 sampled pass nonexact,1 sampled pass with debt nonexact,1 semantic unavailable.
No integration, not a fresh-only cohort. OSS control-history experiment now runs
from the unchanged failing PFS seed,2 logical calls/high reasoning/timeout600,
with no agent-supplied fix (`pfs-release-oss-control-feedback-v1`, handle8136).

HUD ABI blocker identified as sprintf's variadic prototype. A ValueError from
configured callee ABI selection now becomes a named unresolved contract and
explicit panel debt, retaining the existing non-authoritative diagnostic arity
fallback; it does not select an inactive declaration or relax entry ABI checks.
No sprintf effect or complete variadic argument model is thereby established.
Unchanged HUD replay `hud-callee-abi-debt-v1` is running (handle89558). Full suite:
1343 passed/18 skipped.

Assisted HUD compile probe now passes compiler/frontend after coordinated mixed
global view and buffer/end-sentinel changes (`hud-mixed-view-buffer-assisted-v1`).
It is nonexact, and semantics are unavailable at configured-header complex ABI
resolution. This is a diagnosed compile representation gap, not an autonomous
solve. Stack array extent12 is an explicit frame-space hypothesis, not recovered
original C. Worker-level transport test also verifies retry events survive JSON
and proposal recording, not just the HTTP client.

Transport observability: llm.generate records each HTTP attempt, per-socket
timeout, elapsed time, response/error type and HTTP status when available.
Failure exceptions retain their original type plus transport_events; modelrepair
and agentrepair persist these separately from logical calls. Cached generations
record zero current requests and retain original transport history separately.
Campaign audit exposes the records. Retry semantics are unchanged: up to3
transient attempts with2s/4s backoff, so timeout240 can take about726s before
overhead. Frozen older receipts do not retroactively gain measured attempts.

Campaign audit now carries source/job-bound generation-failure events separately
from invalid proposals and compile errors. HUD reasoned-alternative receipt
1788727043235971403 contains one attempted call and a TimeoutError, not a model
repair. Mixed-width global/member representation and stack-buffer end aliases
remain concrete compile gaps; timeout is not evidence that OSS rejected their
correct repair. See FAILURE_COVERAGE.md for binary offsets and receipt hashes.

Historical format issues resolved: strict legacy per-function JSONL parsing
retains function/exact/draws records; explicit empty connected-cluster manifests
record zero selections. Selected DAG census nodes are now exclusion inputs too,
without sweeping unrelated inventory metadata into exposure. Inventory v3 reads
186 selection receipts,528 exposed names,zero unreadable/rejected selections.
Other schema/external-inspection debt remains explicit; no fresh validation claim.

Historical exposure inventory now scans top-level result receipts and records
known selection schemas, hashes, unknown schemas, unreadable files and rejected
selections separately. Older differential/frozen wavefronts and connected DEV
clusters are accepted exclusion inputs; configured targets count even before
their first completed node. `historical-selection-exposure-inventory-v1.json`
finds141 selection receipts and528 exposed names (three DB histories included),
but records5 unreadable and2 rejected selections: fresh-history completeness is
NOT certified. CLI: eval.experiments.campaign-gap-audit.exposure_inventory.

Leaf admission now has execution witnesses for every one of its62 allowed
opcodes (`tests/test_leaf_opcode_execution.py`). Audit found/fixed missing
mthi/mtlo/neg execution; signed neg overflow remains explicitly unsupported.
These are smoke witnesses plus targeted value/provenance checks, not proof of
every opcode/operand/exception path. Full suite1332 passed/18 skipped.
Fresh-cohort exposure input also accepts historical resumable campaign receipts,
including parked/unattempted nodes. The old24-function campaign adds3 exclusions
missed by attempt rows plus paired-selection receipts; see FAILURE_COVERAGE.md.

Current-range __osGetId environment audit reused the existing OutputBuffer
mechanism, not a new controller: assumed successful __osContRamRead output32
bytes exposed an admitted-but-unimplemented `not` in the concrete checksum.
Runner now executes `not` and `nor` with32-bit results and input provenance.
`getid-output-environment-probe-v1/v2.json` bind the unchanged candidate and
paired opaque/assumed environments. After the fix, the assumed arm reaches
88.67% instructions/76.92% edges versus20.67%/19.23% without the output model;
both retain64 sampled passes WITH debt, not exactness or universal semantics.
Unresolved stack aliasing remains refused. Full suite1267 passed/18 skipped.
Output assumption remains explicit experiment-only, not silently auto-enabled.

Missing-call debugger feedback now includes bounded, independent branch-decision
histories from both executions since their last matching call checkpoint, plus
the terminal window for the side lacking the call. It does not assert branch
alignment or waive semantic gates. Live `pfs-release-control-feedback-v1.json`
preserves63 passes/1 failure while exposing target high-byte0 versus candidate
low-byte3 at the loop guard. An explicitly assisted one-expression correction
(`pfs-release-loop-byte-assisted-v1.json`) passes64/64 on the same new panel;
still nonexact, finite-input/opaque-callee debt, no OSS success claimed.
Full regression suite:1266 passed/18 skipped.

Conditional header ABI selection is now wired into DeferredPanel/Panel for
entry and call contracts. All textual variants are considered; ambiguous
contracts need the configured header-only compiler probe, never first-match
authority. Failed initialization retries when includes/target artifacts change.
O32 argument words 4-15 receive explicit stack seeds; pointer words get mapped
synthetic regions, with pointee mutation support and explicit validity debt.
Live `pfs-release-active-abi-replay-v1.json` on unchanged assisted source now
explores 141/141 modeled instructions and 26/26 branch edges, versus roughly
30%/19% before. Its 64-case panel exposes one failure (63 passes): a missing
fourth __osBlockSum call followed by premature output/return. This is not a
semantic solve or byte exactness. The old low-coverage receipts remain intact.
Remaining debt: stack scalar branch-directed mutation, C object extents/aliases,
opaque callees, complex ABI forms, and transfer validation. See FAILURE_COVERAGE.md.

Differential status-mismatch feedback now includes both bounded terminal
windows, including the returned side's comparison window, rather than only
the faulting side. Live unchanged-source replay retains all27 failures while
exposing the successful guard path. An assisted first high-byte correction
on __osPfsReleasePages separately changes37/64 to64/64 passes on the same
pre-change panel, with only30%/19% instruction/edge coverage and nonexactness.
See `pfs-release-high-byte-assisted-v1`, `pfs-release-status-comparison-v1`
and FAILURE_COVERAGE.md; no autonomous solve or semantic proof. Full suite:
1260 passed,18 skipped. Frozen running campaign is unchanged.

Latest controlled SDK experiment: recovery transfer to __osRepairPackId remains
blocked on inconsistent aggregate stack reconstruction. An explicitly
supervisor-assisted __osPfsReleasePages header-array/union view plus correct
prototype compiles and passes frontend, but only37/64 sampled cases pass with
low coverage/opaque-callee debt. Receipts `recovery-transfer-packid-v1` and
`pfs-release-header-view-assisted-v1`; not autonomous recovery or exactness.
This isolates missing representation work without supplying reference bodies.

The coordinated type editor's deterministic packet (`type_transaction.packet`)
now also rejects assignment suffixes such as `*p = q` and `obj.p = q` as direct
variable copies. Unsupported forms are retained with source slots; genuine
multiple standalone assignments on a line remain indexed. This complements
the earlier constraint-solver lvalue fix. `typepacket-lvalue-audit-v1.json`
binds the saved alEnvmixerParam source/assembly and confirms four direct hints
plus four explicit declines. No additional model success inferred. Full suite:
1260 passed, 18 skipped; immutable ROM baseline hash unchanged.

Stack byte-subfield hypotheses now run in resilient `modelrepair.normalize`
through `stack_buffers.byte_subfields`. An undeclared m2c unkspNN read can
become a char-pointer byte view of one uniquely containing u16/s16/u32/s32
spNN local only with matching fixed-frame full-scalar stores and byte loads.
Signedness comes from lb/lbu. Source-name correspondence remains a hypothesis;
no guessed KB fact or semantic exemption. Ambiguous owners, missing accesses,
mixed load signedness, dynamic frames and non-read uses decline. Receipts retain
source/assembly hashes and instruction indices. Live pfs-release replay removes
both undeclared byte aliases, but header ABI/aggregate indexing still prevents
compilation. Full suite 1258 passed/18 skipped, then six focused tests pass
including an added real-worker activation/lineage regression.

Verified saved result: `alSynSetPitch-exact-audit-v1.json` binds candidate,
checkpoint and exact same-link object certificate; strict frontend passes,
144 text bytes with zero positional differences. Not whole-ROM integration.
Batch 2 remains live; aggregate accounting waits for a terminal checkpoint.
Full suite after the recovery-order adjustment: 1257 passed, 18 skipped.

Post-freeze resilient worker wiring: `eval.agentrepair` now invokes existing
`compile_recovery.variants` for a noncompiling recovery seed, scores distinct
bounded alternatives with original parent IDs, and feeds the best retained
context into type constraints. Recovery uses the original root so metadata
projection does not hide changed headers and suppress m2c redrafting. No new
recovery generator/controller. Zero-model live `recovery-wire-pfs-release-v2`
records both header and redraft candidates; still noncompiling. V1 preserves
the ordering failure. Unit activation/ordering test passes; full suite before
the ordering adjustment: 1257 passed, 18 skipped, then seven agent tests pass.
Frozen campaign is unchanged. See FAILURE_COVERAGE.md for remaining SDK causes.

Post-freeze exposure repair: paired `run_expansion --historical-cohort` reads
explicit prior paired/expansion selection receipts, binds their hashes and
selected names, and resolves parked identities through historical inventory
addresses even when attempts are empty. Corrected extraction requires these
receipts as well as historical DBs. Unknown/malformed receipt schemas fail.
`cohort-exposure-audit-v1.json` confirms `_bcopy` and `__osEnqueueThread`
excluded with actual histories. Full suite: 1256 passed, 18 skipped.
This cannot discover unrecorded inspection; callers must supply all relevant
histories. It does not retroactively change the frozen mixed campaign.

Frozen ROM-range batch 1 completed its budget: 28 model calls, two accepted
object-exact candidates, one sampled-pass/nonexact, five noncompiling and two
parked. Source/job-bound audit: `failure-coverage-rom-batch-1-audit-v1.json`.
Batch 2 remains live; goal acceptance and causal accounting remain incomplete.

Post-freeze layout parser repair: `diffrepair` now counts positive, checked
signed-32-bit literal addition/subtraction array extents, including generated
padding `0x38 - 0x04`. All four field/size/reorder readers share completeness
checks: unsupported members or extents decline the region, not silently shift
later fields. Macros, octal bounds, overflow, bitfields and callback members
remain outside this parser dialect. Tests exercise both offset recovery and
an actual padding rewrite; full suite 1254 passed, 18 skipped.
`range-replay-alEnvmixerParam-layout-v2.json` confirms the same remaining
24/64 sampled passes and no useful layout proposal; parser correctness is not
layout-search completeness. Running frozen campaign code was not changed.

Current live audit: frozen `failure-coverage-rom-code-v1`, paired receipt
`failure-coverage-rom-paired-v1.cohort.json`, experiment DB independently
ROM/range/fact validated. Two previously parked targets have no attempt rows
and escaped historical exposure exclusion. This is a mixed fresh/replay run,
not fulfilled all-fresh acceptance. Add historical cohort exposure before
the next clean selection; preserve this run unchanged. Details and live handle
are in `FAILURE_COVERAGE.md`.

The alEnvmixerParam six-call replay exercised the existing source-edit fallback:
compiler/frontend pass, 24/64 sampled passes, nonexact. Deterministic layout
replay exposed another parser gap: arithmetic array bounds are skipped by
`diffrepair.region_fields`, yielding wrong running offsets. This remains
unfixed and must be addressed before interpreting absent layout proposals.

Type-flow lvalue audit: `type_constraints.solve` now admits only standalone
direct-pointer assignment statements, rejecting and recording suffix matches
such as `*p = q` and `object.p = q`. Previously these falsely merged `p` with
the stored value. Five motivating regressions pass; full suite: 1251 passed,
18 skipped. `range-replay-alEnvmixerParam-lvalue-v1.json` confirms activation
on the corrected-range DB, but still generates no type plans. Cross-case
polymorphic `void *` roles and store-through-pointer typing remain unresolved;
this parser repair is not a compilation or semantic success.

Cross-epoch selection guard: paired `run_expansion --historical-db` excludes
prior attempts by name/address and binds historical exposure sets/cutoffs.
Corrected-extraction databases require explicit history, avoiding false novelty
from empty attempt tables. Clean-copy replays completed: osStartThread reaches
the model past the repaired guard (64 sampled passes, opaque OS callees,
nonexact; one assembly-edit proposal rejected); alEnvmixerParam still fails
void-member typing. Baseline/hash and historical DB remain unchanged.

Clean ROM-bound evidence baseline created:
`eval/results/kb-sbk1-rom-ranges-v1.sqlite` (2113 asm/unattempted inventory rows,
72041 facts, no inference/attempt history). Independent audit finds zero fact or
inventory differences; integrity/FKs/TU metadata pass. Historical DB untouched.
Use experiment copies, not this baseline, for replay. New-DB zero attempts must
NOT reset historical heldout/development exposure. Six old cohort size counts
changed, including two size-stratum switches; fresh validated-size coverage is
still pending. See `FAILURE_COVERAGE.md` for hashes and validation receipts.

ROM-bound range audit is now available as `python -m miner.range_audit` with
repo/db/output arguments. It is read-only and compares regenerated facts and
function metadata to history; `rom-bound-range-audit-v1.json` verifies all 2113
function word streams against the ROM (806 extra historical facts, two missing,
zero changed retained facts). `miner.evidence.extract` now binds ROM words before
DB work and refuses existing DB destinations. Separate extraction/replay is
pending; historical database remains untouched.

Critical post-campaign finding: `miner.evidence.disassemble` previously used
printed objdump labels as function ends, causing overruns/truncations and skipped
zero words. It now uses ELF address/name/size ranges and address-indexed words
with `-z`, failing closed on incomplete ranges. Six WSL tests and full 2113-function
read-only extraction pass. `elf-range-inventory-audit-v1.json` finds 118 DB
size/count discrepancies and 806 existing evidence rows outside ELF extents.
Database unchanged; ROM binding, extraction-epoch repair and downstream
revalidation are pending. See `FAILURE_COVERAGE.md` before more DB-fed runs.

Latest goal checkpoint: both frozen paired batches finished (35 model calls,
19 functions, one accepted object exact; no integration). See
`failure-coverage-paired-audit-v2.json` and `FAILURE_COVERAGE.md`. No workers from
that run remain. A ROM-checked `corrupted` assembly slice conflicts with database
extent and symbol-map address; inventory validation is now an open prerequisite.
Earlier in-progress paragraphs below describe historical checkpoints.

Operational inventory, not a roadmap or a capability claim. Last audited:
September 6, 2026. Update this file when a mechanism is added, wired, disabled,
or tested. Historical experiments remain in their original receipts.

Current broader validation: `eval/experiments/campaign-gap-audit/run_expansion.py`
`--paired-stratified` preselects two disjoint, zero-attempt development panels
across domain, instruction-count and leaf/calling strata before either runs.
Sparse strata are recorded. It delegates execution unchanged to
`completion_campaign`, with heldout guards and no integration. Frozen code
`eval/results/failure-coverage-code-v1`; selection receipt
`failure-coverage-paired-v1.cohort.json`: 19 functions, 10/9 panels. Execution is
in progress, not accepted validation; see `FAILURE_COVERAGE.md` for live handoff.

Post-freeze guard correction: `workspace.assert_uncontaminated` permits a full
prototype only when independently matched in included headers (optional extern
and whitespace normalization); no general declaration/body whitelist. Five
regressions and the saved `osStartThread` live guard probe pass. Full model
handoff replay is pending, and the original heuristic is not a provenance proof.
Audit summarizer now separates sampled passes with execution debt and retains
opaque-stack/unknown-call-arity obligations. Neither change is in frozen v1.

Post-freeze SDK boundary diagnostics: `sdk_intake.classify` retains opcode counts,
terminal-shape and bounded offending-instruction evidence, not just a blanket
control-flow/relocation label. Existing admission is unchanged. Live ROM-bound
probe `sdk-boundary-bcopy-v2.json` reproduces internal/likely branches, multiple
returns and trapping `add` outside the straight-line dialect. The tiny
`sdk_boundary_probe.py` experiment reuses intake, with heldout/no-overwrite guards;
no model or integration. This explains the boundary; it does not solve `_bcopy`.

Post-freeze opaque-layout extension: `compile_obligations.opaque_variant` now
recognizes header-forward-declared bare `struct Tag *` parameters through a
private planner projection, not a public ABI rewrite. Existing definitions
remain protected. `bare-tag-transition-v1.json` confirms zero-model compiler
recovery for `initRaceUiSpinHitTransitionEffect`; strict frontend catches the
remaining struct-pointer scaling/type conflict (0xFC0 vs target byte offset
0x24). Nonexact and semantically untested, despite 99.405 weighted similarity.

Follow-up `byte_pointer_variant` in `compile_obligations` is now wired through
both compile recovery and resilient `modelrepair.normalize`. Unique direct
callsite parameter+byte-offset evidence supports a bounded source pointer-view
hypothesis; no blanket pointer-cast cleanup. `byte-view-transition-v1` preserves
the missing-wiring test; v2 confirms zero-model activation, frontend pass,
64/64 sampled passes and two remaining stack-slot bytes for the transition
case. Opaque-callee/input debt remains. Full suite: 1244 passed, 17 skipped.

Before implementing a new mechanism:

Persistent failure-coverage goal and acceptance ledger: `FAILURE_COVERAGE.md`.
Initial cross-checkpoint baseline: `eval/results/failure-accounting-baseline-v1.json`.
Stage classification is not causal diagnosis or proof of recovery coverage.
The v2 audit additionally checks candidate source hashes, hashes supporting job
receipts, retains model failure telemetry, and emits symptom/owner/next-action
records with causal accounting explicitly incomplete. Owner:
`eval/experiments/campaign-gap-audit/summarize.py`; tests:
`tests/test_failure_accounting.py`. No solver or evidence-tier changes.

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
Scoring now accepts explicit `extra=None` while retaining frontend/recipe
evidence. Consecutive compile-recovery visits are capped at two so changing
header contexts cannot indefinitely postpone a model profile. The cap is
live-tested in v3, not included in the v2 live results. Earlier bounded experiment:
`eval/experiments/campaign-gap-audit/autonomy-progress.md` (eight functions,
24 visits, four model calls, zero exact; one newly compiling candidate).

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
| `eval/uopt_replay.py` | Replays IDO register colouring over every target dump from control-flow webs; reports model/oracle/chrono/random agreement plus order-independent soundness (is IDO's choice free) | Diagnostic only; nothing consumes it. Result 2026-09-12: allocation is not predictable from post-allocation assembly (HYP-20260912-01). Adds the numeric-branch marker `cfg.build` needs for dumps. |
| `solver/codexprovider.py` + `tools/codex_shim.py` | A hosted agent (Codex CLI) behind the SAME `modelrepair` kernel: provider adapter in WSL, HTTP shim on Windows because interop is off here | Experiment provider only. Nothing schedules it; `eval.agentrepair --provider solver.codexprovider:provider` is the only caller. The shim must be started by hand on Windows (`python tools/codex_shim.py`) or every call fails. Not wired into the campaign, and the campaign's frozen snapshot does not contain it. |

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

Current receipt/audit: `eval/experiments/campaign-gap-audit/semantic-gaps.md`.
The v3 frozen campaign completed 24 visits/eight model calls without a crash,
but produced no exact match. The source-to-ABI feed now explicitly flags a
one-word candidate return declaration against an admitted two-word binary
result; one follow-up OSS proposal was rejected without improvement. Full
suite: 1,221 passed, 12 skipped. All workers are stopped.

September 6 semantic-gap follow-up: initialized non-code ELF section symbols
now supply literal bytes (bounded to 256 bytes, relocated sections decline).
Call feedback marks equal normalized string contents as equal even when physical
addresses differ. Repeated stack-buffer callsites can bind only when every
occurrence agrees on the same source local and target stack slot; byte-pointer
casts are supported without permitting value uses or ambiguous bindings.

The concrete callee loader also admits one closed 12-instruction o32 word-pair
multiplication dialect with 64-bit intermediates and both v0/v1 outputs. The
entire instruction stream must match, then reassemble to the ROM; no callee-name
dispatch or general 64-bit support is claimed. Recognized header integer-pair
arguments consume two words; sparse alignment padding remains explicit debt.
Nested feedback includes a bounded suffix as well as prefix, exposing return
construction. Initial zero-model receipts: `semantic-gaps-paired-v1/v2.json`.

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
| `solver/m2c_context.py:lower_bitcasts` via `repair_context.normalize` | Lower explicit m2c bit reinterpretations to union expressions at their original evaluation point; narrow integer operands promote to a 32-bit word. Parenthesized integer expressions feeding f32 use an unevaluated C89 integer-category/32-bit-width guard and unsigned-word union storage; calls/increments execute once. | Reused in fresh m2c intake and resilient recovery of noncompiling roots/children. Never substitute numeric float conversion. Complex float-to-integer operands and partial/postfix operands still decline; invalid integer categories fail compilation rather than silently truncate. `bitcast-expression-alSynAllocVoice-v1.json`: attempt 30004 -> 30544, compiler/frontend pass with zero model calls, 71.2 weighted score (not byte percentage), nonexact, 64 differential disagreements with opaque-callee/coverage debt. Compiler/differential verification remains required. |
| `eval/semantic_lane.py` | Existing target-only coverage explorer (5,000 probe budget), retained coverage cases plus stress panel (64-case floor), same frozen cases for every candidate, full-entry differential replay. Retains call-arity authority and executed opaque-stack-pointee obligations (side, ordinal, argument, peer address, case counts). | ABI/callee/coverage debt explicit. Raw stack-address mismatches may reflect layout sensitivity; equal addresses can conceal different pointee bytes. Sampled passes with these obligations are `observed_pass_with_execution_debt`. No mismatch is waived and no object/alias mapping guessed. Obligations and arity authority reach the bounded model packet. Live: `opaque-stack-alSynAllocVoice-v1.json`, unchanged source, 64 disagreements, allocator arity explicitly unresolved. |
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
# Binary data and measured tiny-function wiring (2026-09-13)

The frozen fast campaign now calls `campaign_data.prepare` under its existing
lock at drained startup. A content-addressed artifact in `RUN/binary-data/`
contains a pinned `binary_data` catalog, bounded per-function prompt packets and
eligible read-only data contexts. Inflight recovery reuses its prior verified
artifact without rebinding jobs. Only changed unresolved-node data identities
renew evidence keys; exact/integrated nodes are preserved.

`fast_campaign` dispatch config -> `completion_campaign.execute` prompt and memory
context -> `agentrepair.run` -> `semantic_lane.DeferredPanel/Panel` ->
`data_memory.apply` before coverage exploration and panel hashing. Read-only ROM
spans become initialized-byte annotations, not inferred C extents. Target-owned
initializers are retained and candidate-owned data overrides the target fallback.
The dashboard reads catalog summary metadata through `/api/data`; polling never
opens the catalog or attempt database. `eval.data_matching` provides explicit
region-byte comparison without automatic matching promotion.

The existing bounded `isolated_register_web` family now includes
`byte_test_inline.candidates`: fuse a uniquely assigned byte temporary into its
immediate zero test with matching byte-pointer type. Actual generator replay
matched `strlen`; extra uses, volatile accesses, shadows and intervening
operations decline. Existing deduplication and candidate budgets are unchanged.

The function-boundary checker independently resolves intact MIPS relocation
pairs against pinned ROM/link addresses; object exactness is not relaxed.
Integration preparation can retain declaration-only externs after an actual
project-context Clang AST probe. Full-TU compatibility and combined whole-ROM
verification remain required. Pilots found and corrected an unsigned-global
declaration and an SDK scheduler-sentinel type view that isolated scores missed.

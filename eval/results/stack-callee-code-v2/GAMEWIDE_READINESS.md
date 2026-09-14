# Game-wide automatic matching: September 4 audit

The objective is byte-exact recovery of the whole game, not one callback.
Exact source for this known matching project is a plausible destination; the
current pipeline does not establish unattended recovery of every function.
General binary-only decompilation is a stronger claim than reconstruction with
this project's compiler, symbols, headers, and linker environment already known.

## Inventory snapshot before the fresh intake probe

- 2,113 function records; 439 had at least one logged attempt, 1,674 had none.
- 195 functions had historical exact flags: 137 classified SOLVED, 55 reference
  recoveries, 3 separately classified header-assisted. SOLVED does not mean
  autonomous, and these historical flags were not all recertified under the
  newer section/relocation certificate during this audit.
- All 2,113 `functions.state` values say `matched`: that is imported reference
  project state, not the automatic solver's accomplishment.
- Do not turn any of these counts into a percentage of autonomous completion.
  Header assistance, frontier-agent intervention, library code, function size,
  and older verification standards require separate accounting.

## Fresh intake test

`python -m eval.gamewide_probe --repo ... --db ... --output ...` preselects one
previously unattempted non-held-out function in each of four strata: leaf/caller
and 20–80/81–200 instructions. Selection uses address order and metadata, not
observed ease of solving. A failed function is not replaced by an easier one.
This is an intake diagnostic, not a representative success-rate benchmark.

| Selected function | Stopping point |
|---|---|
| osWritebackDCache | Workspace launcher cannot find a same-named assembly file |
| __sinf | Same filename-based target lookup failure |
| requestControllerRead | Fresh assembly-only m2c draft does not compile |
| initControllerSubsystem | Fresh assembly-only m2c draft does not compile |

No LLM was called. In particular, these compilation failures do **not** establish
that an LLM or the full repair search cannot solve the functions. The probe uses
only bounded binary-backed declaration adaptations, not all available repair
stages. Contextual bootstrap drafts are discarded; m2c is explicitly rerun with
assembly only. Compilation and ABI inspection still use project headers.

The first census crashed because failed C compilation had not produced
`target_object_dump_normalized.s`. The census now records a compilation-stage
node without requiring nonexistent object dumps and continues its panel.
The two identical roots were replayed successfully through this reporting path.

Receipts:

- `eval/results/gamewide-fresh-intake-v1.json`: initial extraction stop.
- `eval/results/gamewide-fresh-intake-v2.json`: all four intake outcomes, followed
  by the original census error. Kept unchanged as negative evidence.
- `eval/results/gamewide-fresh-intake-v2-census-recovery.json`: repaired census,
  two cleanly recorded compilation failures, no semantic or exact successes.

## What stops all-function automation

1. **Universal intake and compile context.** Resolve functions by binary
   address/symbol identity rather than assuming a same-named `.s` file exists.
   Model ordinary declarations/calling conventions mechanically where possible,
   and route genuinely unknown declarations to bounded inference.
2. **Program-level execution.** Call signatures and dependency ordering are not
   executable callee semantics. Real side effects, shared memory, aliases,
   recursive components, indirect calls, floating point, hardware-facing SDK
   functions, and meaningful initial states remain major work.
3. **Compiler-shape search.** Passing differential tests does not identify the
   C expression/loop/lifetime arrangement that reproduces IDO's register
   allocation, instruction scheduling, jump tables, and padding. Keep compiler
   experiments and diverse source candidates, not just higher similarity scores.
4. **Measured transfer.** A new rewrite that fixes its motivating function is
   development evidence. Freeze it and test on different functions, preserving
   failures, provenance, and denominators. Do not generalize callback success
   to all functions or reject the whole approach from a small intake probe.
5. **Integration authority.** Function object equality is not whole-ROM equality.
   Translation-unit boundaries, shared declarations, data/rodata, relocations,
   linker layout, and the final ROM must be rebuilt and checked. The isolated
   integration gate exists but was not run against game replacements here.

Keep semantic recovery and exact matching as parallel work queues. Do not wait
for universal semantic proof of the whole game before banking independently
certified exact matches. Conversely, never promote sampled semantic agreement
into the matching build. SDK/handwritten assembly may need an explicit separate
policy; retaining assembly is not an autonomous C-decompilation success.

## Implemented intake repairs and measured transfer

The September 4 follow-up added reusable mechanisms, not hand-written target
function bodies:

- `solver/target_intake.py` resolves aliases using equal linker addresses and
  actual assembly labels. An isolated alias target retains its original
  path/hash/address; strings and unrelated symbols are not renamed. Ambiguous
  targets and existing incomplete workspaces are not overwritten. SDK macro
  assembly is explicitly routed to a separate backend requirement, not counted
  as C recovery.
- `solver/m2c_input.py` normalizes O32 floating-point register spellings for m2c
  without altering the oracle target. Missing/failed workspace drafts can use
  the same assembly-only fallback through `workspace.m2c_draft`.
- `solver/project_headers.py` reconciles generated top-level declarations
  against included headers, understands SDK C-linkage wrappers, avoids unioning
  every alternative declaration header, and proposes unknown extern types from
  simple address arguments to known pointer parameters. Original drafts remain
  separate candidates. These are compiler-context hypotheses, not inferred
  object extents or semantic proofs. No target C bodies are read by these steps.
- `eval/gamewide_probe.py --replay PRIOR_RECEIPT` keeps the exact original
  selection, checks its metadata/held-out exclusions, and labels the run as
  development replay. Failed compile handoff favors removal of unknown
  declarations instead of always handing the original syntax-error draft back
  to the next stage. All attempts, including failures, remain recorded.

### Same-cohort result

Receipt: `eval/results/gamewide-intake-replay-v5.json` (zero model calls).
Earlier v3/v4 failures are retained, not overwritten.

| Function | Result |
|---|---|
| requestControllerRead | **100%, independently certified object-section/relocation exact**, attempt 28080; census recompile also exact |
| initControllerSubsystem | Declarations repaired; next errors are array lvalues and invented `.unkN` selectors; still noncompiling |
| __sinf | Alias target extracted and mixed register spellings normalized; next blockers are `bitwise` pseudo-C and unknown rodata layout |
| osWritebackDCache | Located SDK macro assembly, explicitly requires a separate assembly/hardware backend |

The exact result is **header-assisted** and verifies a function object in the
same link environment, not a whole ROM or universal binary-only recovery.
Candidate source SHA-256 for attempt 28080:
`2e4d26b229e251cee6b0a232ceea744736a62811924b7f5e2bf10e2d62633d20`.
No game translation unit was replaced or integrated during this work.

### Frozen transfer check

Receipt: `eval/results/gamewide-fresh-intake-transfer-v6.json`. Address-order
selection chose three previously unattempted functions plus the previously
probed cache routine (extraction failures have no compiler attempt, so the
current selector can select them again). No easy replacements were substituted.

- `__cosf`: alias extraction and register normalization transferred; draft still
  fails on unknown rodata and `bitwise` syntax.
- `probeControllerPak`: known prototype/global reconciliation and queue-object
  inference reduced declaration errors; unresolved objects remain.
- `serviceRumbleMotorRequest`: header reconciliation reduced declaration errors;
  one unknown extern declaration remains at the current stopping point.
- `osWritebackDCache`: same explicit separate-backend classification.

**0/3 new functions compiled or matched in this bounded intake test.** This is
negative end-to-end transfer evidence for these intake mechanisms alone, not a
failure of an LLM repair attempt: no model was invoked. Semantic testing could
not adjudicate these noncompiling drafts. The test excludes formal held-outs
and is not a representative game-wide success-rate estimate.

Next priority is a compile-repair handoff for unresolved object/layout evidence
and m2c pseudo-C, followed by differential repair once compilation works. Record
intake outcomes separately from compiler attempts so unsupported SDK entries do
not repeatedly occupy the next fresh stratum. Keep original cohort failures in
the denominator when measuring an improved pipeline.

## Pipeline-gap repair: header-first decompilation and automatic admission

The three v6 compilation failures were replayed without changing their cohort.
The effective fix was to move declaration context **before m2c**, not to delete
apparently extra arguments from generated C or blanket-cast pointer arithmetic.

`solver/m2c_context.py` now supplies:

1. Header-only preprocessed context, using the project's existing m2ctx helper
   without invoking its shared `ctx.c`-overwriting CLI. The context wrapper has
   no target function body. m2c uses SDK arities and project array declarations
   while recovering the function, producing three-argument SDK calls and typed
   indexing directly.
2. One bounded declaration-feedback pass. Typed local pointer assignments
   constrain unknown extern array element types, without inventing extents.
   Homogeneous literal float tables in binary-extracted assembly constrain
   rodata declarations, with path/hash/directive evidence. Those are contextual
   hypotheses, not writes to the binary evidence tier.
3. Narrow lowering of explicit 32-bit `bitwise` pseudo-C to IDO-compatible local
   union reinterpretation at the original expression evaluation point. No
   numerical float-to-integer conversion or inline assembly is substituted.

Raw drafts and failed context branches are retained. Unsupported expressions,
conflicting types, ambiguous data files, and mixed rodata directives decline.
Tests cover name collisions, comments, complex expressions, delay-slot
annulment, and context injection/overwrite boundaries.

The wavefront previously skipped noncompiling roots before its m2c stage.
`eval/compile_intake.py` now runs before that eligibility check: at most four
compiler candidates, complete attempt lineage, then a new single-function
census. Original DAG dependencies are retained, and later workers receive the
new census/attempt rather than stale failed-root data. A compiled function with
no executable semantic cases is still withheld from differential repair.

### Actual wavefront replay, not just a standalone adapter test

`eval/results/frozen-wavefront-compile-intake-v2.json` starts from the **original
noncompiling v6 census**, with solver/header/assembly inputs frozen, zero repair
rounds and zero model calls. All three roots recover compilation automatically.

| Function | Assembly similarity | Differential outcome |
|---|---:|---|
| probeControllerPak | 86.708 | 9/9 observed cases pass; all 14 conditional outcomes and 71 reachable target instructions covered under opaque SDK hooks |
| serviceRumbleMotorRequest | 95.489 | 10/10 observed cases pass; all 14 conditional outcomes and 92 reachable target instructions covered under opaque SDK hooks |
| __cosf | 28.173 | Compiles; target execution is unsupported at `c.lt.s`, so semantic correctness is not asserted |

These scores are the oracle's assembly-similarity metric, **not percentages of
bytes proved correct**. None of the three is byte-exact. Both controller results
remain observational because `osPfsInitPak`/`osSendMesg` side effects are opaque.
Full structural branch coverage is not a universal input-domain proof.

The debugger's integer branch-likely predicate gap (`beqzl`, etc.) was fixed;
the existing annulled-delay-slot behavior is preserved and tested for taken and
untaken outcomes. This exposes the next honest cosine limitation: floating-point
comparison/condition handling, not a failed C draft or a model failure.

The final wave records 6.498 seconds of compile intake and 7.283 seconds summed
worker time (not total process wall time, which includes pinning/orchestration).
Its two differential child runs pass the existing lineage/held-out audit.
Validation: **1,024 passed, 9 skipped**.

Regression receipt `eval/results/gamewide-context-regression-v9.json` keeps
`requestControllerRead` certified exact and also gets `__sinf` compiling via the
same mechanisms. Sine stops at unsupported `cvt.d.s`; `initControllerSubsystem`
still has two invalid scalar-to-struct stores from m2c, and the cache routine
still requires a separate hardware/assembly backend. No game C translation
units or shared data definitions were changed or integrated.

## Independent exactness lane and persistent intake (2026-09-04)

The wavefront now sends compiling roots with no conclusive execution cases to
`eval.agentrepair` instead of skipping them. `--exactness-only` selects that lane
even for executable roots. It first tries up to 32 deterministic source rewrites
(when rounds > 0), then a bounded local-model edit tree. In this lane `--rounds`
is a strict model-call cap, not the differential worker's diagnosis/patch cycle.
Zero rounds still reverify the source and make no model calls.

The byte-only lane explicitly does **not** carry semantic-pass/coverage claims
from the old census onto its new source. Its terminal authority is the existing
section/relocation certificate, not assembly similarity or sampled semantics.
Prompts get each parent's new residual; the root diagnosis is not reused after
an edit. Compiled experiment outcomes join the prompt history. Model-generated
object filenames are run-specific, so later experiments cannot overwrite them.

The existing diverse search frontier is now exported with source hashes, attempt
IDs, and hypothesis paths. `--parent-wave` re-verifies retained alternatives
before expanding them again. Best source and all attempts remain in the ledger;
an unsuccessful bounded search is parked, not declared impossible. Successful
edits remain proposals/attempts, not automatically promoted catalogue rules.
Use the existing generalization/principle panels to check transfer before adding
a reusable generator; development-cohort reuse is not held-out validation.

Fresh intake has an evaluation-only `eval_intake_outcomes` checkpoint table.
Extraction, assembly-backend, and draft failures are remembered even when they
never produced a compiler attempt. Unchanged inputs no longer reselect them;
changed solver/header/binary inputs or explicit `--replay` allow another try.
`eval.gamewide_probe --limit N` selects unattempted functions across all sizes,
leaf-first, rather than restricting intake to the original four size strata.
The resulting census can go directly into the wavefront. Frozen held-out names
remain excluded. This is bounded batch processing, not a perpetual daemon.

Wave receipts include explicit failure categories and an `integration_queue`.
Only source-hash-bound exact certificates enter it. They still require prepared
translation-unit replacements and `eval.integration_gate`'s isolated ROM build.
The wave itself always reports `whole_rom_verified=false`: individual matches
do not silently count as whole-game completion or automatic source integration.

Example bounded repair of the current three-function development cohort:

```sh
python3 -m eval.frozen_wavefront \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --census eval/results/gamewide-fresh-intake-transfer-v6.census.json \
  --output eval/results/frozen-wavefront-independent-exactness-v1.json \
  --functions probeControllerPak serviceRumbleMotorRequest __cosf \
  --rounds 3 --exactness-only --timeout 420 --num-predict 6000 \
  --coverage-cases 512 --stress-cases 128
```

Do not overwrite an existing receipt: use a new output name for each experiment.

### End-to-end batch and output-completion experiments

`eval.gamewide_batch` is the single-command fresh-function entry point. For example:

```sh
python3 -m eval.gamewide_batch \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --output eval/results/my-next-gamewide-batch.json \
  --batches 3 --batch-size 8 --rounds 3 --exactness-only
```

It runs bounded intake batches, feeds each resulting census to frozen repair,
and records pre-compiler failures as well as repair outcomes. A new invocation
with a new output path advances the persisted fresh queue. Repairing previously
attempted functions still uses their census and the wave's `--parent-wave` path;
fresh intake is not a claim that every stalled function is automatically retried.

Actual zero-model smoke test `eval/results/gamewide-batch-independent-v1.json`:
24 selected entries across three batches; 21 assembly/backend blockers, one
padding-symbol extraction failure, and **two new project-header-assisted exact
matches**, `countActiveMusicSequences` and `countActiveSoundPlayers`. Neither was
an LLM-generated or binary-only match. Their individual objects were subsequently
reverified in `eval/results/frozen-wavefront-new-exacts-reverify-v1.json` (attempts
28308 and 28309), and both enter the source-bound integration queue. No real game
translation unit was replaced and no whole-ROM success is asserted.

The initial three-function exactness experiment improved `probeControllerPak`
86.708 -> 92.620 with the existing deterministic redundant-mask rewrite, but
produced no new exact matches. `serviceRumbleMotorRequest` stayed at 95.489 and
`__cosf` at 28.173. All three reached compiler-only repair, including unsupported
floating-point execution. Its audit found 41 attempt rows, 41 parent edges and
no held-out overlap. The wave v1's old aggregate omits deterministic candidate
counts; use its child receipts rather than interpreting zero model children as
zero compiler experiments. The final implementation reports both separately.

The runs exposed a second engineering failure: GPT-OSS could exhaust 6,000
tokens in unfinished reasoning without emitting an edit. A first completion
retry still used `think=false` and did not reliably resolve this (v2: five
incomplete responses across six provider calls). GPT-OSS ignores boolean
thinking controls; it requires low/medium/high. The final transport normalizes
`false` to `low` for GPT-OSS, and the exactness completion phase explicitly uses
low effort plus a JSON schema. Schema changes are included in generation-cache
identity. See [Ollama thinking controls](https://docs.ollama.com/capabilities/thinking)
and [structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

An unfinished response is now logged as `incomplete-response`, not a bad C
candidate or a semantic failure. One completion retry fits inside the same
provider-call budget; it cannot silently extend the search. These observations
do not establish that more reasoning always helps or that the model cannot
solve these functions.

Final handoff check: `eval/results/frozen-wavefront-independent-exactness-v4.json`
uses the documented chat endpoint for schema-constrained completion. The initial
6,000-token reasoning response was incomplete; the 3,989-token completion emitted
a finalized JSON edit, which compiled and improved Controller Pak 92.620 ->
**92.831**. The edit simplified local declarations/temporaries. It remains a
single-function model proposal, not a promoted generic rewrite. No semantic
replay of that new source or exact-match claim is made. The seven-attempt child
run has seven parent edges and no held-out overlap. v3's generate-endpoint schema
attempt returned empty output; it is retained as negative runtime evidence, not
a failed C implementation. Empty responses now have their own explicit status.

Final validation: **1,035 passed, 9 skipped**. Remaining work is real exactness
search and transfer validation, SDK/assembly backend policy and implementation,
semantic execution improvements where useful, and prepared TU integration plus
whole-ROM verification. These pipeline changes do not guarantee all-function
completion and do not automatically manufacture new generalizable patterns.

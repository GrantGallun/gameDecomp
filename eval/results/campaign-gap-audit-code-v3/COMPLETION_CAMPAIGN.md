# Resumable completion campaign

`eval.completion_campaign` is the persistent controller around the compiler/residual
repair engine. It is **not a guarantee that every function will converge**. Budget
exhaustion, exhausted strategies, object matches and integrated matches have
different statuses. `complete_c_decompilation` remains false.

Current workers enable resilient compile/semantic repair and retain both lane
champions. New configuration/code pins require a new campaign or explicit fork;
the historical v11 checkpoint is unchanged. See the current wiring in
`PIPELINE_MAP.md` and the bounded replay results in
`eval/experiments/resilient-repair/README.md`. No new cohort or all-function
success is implied by these worker tests.

The subsequent type-constraint worker replay unblocked `alLoadParam` from its
original m2c draft without a model call or the prior hand-written typed scaffold.
Selected attempt 29693 reproduces 29682's source and passes 64 automatic cases;
that source also passes the pre-existing 580-case DEV audit. It remains nonexact
(72 positional text bytes differ out of 480). See
`eval/experiments/type-constraints/README.md`. This is a standalone worker result,
not an adopted update to the old 24-function campaign checkpoint.

Run under WSL from `/mnt/c/Code/gameDecomp`:

```bash
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state eval/results/my-campaign.json \
  --functions probeControllerPak serviceRumbleMotorRequest \
  --max-work-items 12 --model-calls 2 --integrate
```

Repeat the same command with `--resume` to continue. `--max-work-items` is a
per-invocation work budget; `--model-calls` is a per-model-strategy call limit.
Omit `--functions` for the full DB inventory minus frozen heldout functions.
Use `--model-calls 0` for zero-model compiler/differential operation. Model calls and instruction
scores are recorded separately; a similarity score is **not percent bytes exact**.

Repair calls receive referenced declarations from included headers as read-only
context. Invalid edits get at most one correction opportunity inside the same
call budget. An attempted edit to assembly/diff text is explicitly diagnosed as
editing the wrong artifact. This does not make every model proposal applicable
or correct; all such failures remain visible in the receipts.

## State and strategy policy

Compile/frontend failures first receive an explicit zero-model `compile_recovery`
visit through the resilient worker (context, bounded type constraints and C89
normalization). This is independent of the model budget and instruction rewrite
eligibility, and runs once per unchanged source hash before model fallback.
`model_visits` counts model-enabled strategy visits, not actual provider calls;
the worker receipts contain the latter. Changed code still requires a new campaign.

Each function receives intake, bounded local rewrites, schema-constrained model
patching, deeper rewrite composition, and high-reasoning alternatives. Selection
is fair by visit count, then callee-first DAG/SCC level and instruction count.
No unchanged source/profile pair is deliberately replayed. An improved source
becomes eligible for earlier strategies again. Non-best alternatives are retained
and reverified by the search engine. A parked function does not block siblings.

The state file is atomically replaced under an exclusive lock. Every work item
has its own durable receipt. Completed receipts can be recovered after an
interruption. A crash during a model call can duplicate that call on resume;
this is not an exactly-once executor. Compiler, headers, ROM, code and discovered
targets are pinned. Changed inputs pause the campaign rather than silently
reusing stale exactness claims.

For a deliberate solver revision, preserve the old campaign and fork:

```bash
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state eval/results/my-campaign-v2.json \
  --fork-from eval/results/my-campaign.json --max-work-items 12 --model-calls 2
```

A fork carries best attempt IDs, not exact verdicts or exhausted-job decisions;
intake revalidates them. Alternatively, `--seeds path.json` explicitly imports a
`function -> attempt ID` mapping. Historical seeds are development replay, not
clean autonomous recovery. Without seeds the campaign starts from assembly/m2c
and header context, including on previously attempted functions. It does not
silently pick legacy DB “matched” rows or recovered reference source as winners.

## Remaining categories and integration

`solver.sdk_intake` uses the ROM slice, address/size metadata and segment mapping
to admit ordinary relocation-free SDK routines. Every instruction must decode,
and reassembly must equal the entire slice. The initial backend is deliberately
limited to straight-line integer routines ending in `jr ra` plus its delay slot.
Hardware instructions are reported by opcode/address. Unsupported control flow,
relocations or special-register ABI cases remain explicit blockers. Retained
assembly is never counted as recovered C.

`solver.compiler_recipe` now resolves per-TU C flags, optimization, ISA, include
paths and compiler driver from an assignment-only projection of the actual
Makefile. It executes no original Makefile recipes. Immutable workspace-local
compiler scripts preserve the existing helper guards, and receipts bind the
recipe and source. Unlogged deterministic baselines reuse the same recipe.
Ordinary C recipes cover 2,067 of the 2,113 inventoried functions; this is
configuration coverage, **not a solved count**. Seventeen functions need special
ABI/postprocessing support. Twenty-nine assembly-origin functions have only
experimental neighboring C settings, not known original C compiler recipes.
Changing those settings can make a candidate worse; old attempts remain stored.

Intake routes rejected do-loops through the existing conservative for/break
lowering, without relaxing compiler guards. Historical seeds retain the original
source and also receive header-only preflight variants, so a tied compile failure
does not permanently discard a useful header-context branch.

`--integrate` prepares source-bound exact candidates as narrow function-only TU
replacements, then invokes `eval.integration_gate` on a disposable WSL copy.
The working game repository is not edited. Shared declarations, functions inside
conditional preprocessing, assembly TUs and other unsupported integration forms
are refused, not guessed. Unrelated conditionals elsewhere in a TU are preserved
and no longer prevent replacement of an unconditional function.
The gate performs the real clean/extract/build and compares the complete ROM.
The build log and rebuilt image are archived alongside its JSON receipt, because
WSL temporary workspaces may not survive subsequent tool invocations.
Matching two replaced functions inside the existing game is integration evidence
for those two, not evidence that the remaining game was autonomously recovered.
Game source files are hashed as opaque build inputs so unrelated TU changes also
invalidate campaign resume; their bodies are not fed into repair prompts.

This controller currently runs compiler-first exactness search. A non-exact
candidate is not labelled semantically correct; use the existing differential
wavefront for source-bound execution evidence. Exhausting these profiles does
not rule out stronger inputs, compiler configurations, or new repair strategies.

## September 4 development audit

Fixed cohort: `__osGetSR`, `__osPopThread`, `__cosf`, `probeControllerPak`,
`serviceRumbleMotorRequest`, `countActiveMusicSequences`, and
`countActiveSoundPlayers`. No heldout target or reference function body was
provided to the repair model.

- v2 exercised pause/resume through all four profiles: 23 work items, 166
  deterministic candidates and 14 model calls. Four candidates exhausted the
  configured strategies without a new exact match. This is a stall, not a
  completed game or proof those functions cannot be solved.
- v3/v4 tested read-only header context, bounded edit correction and explicit
  separation of assembly evidence from editable C. The final v4 model pass used
  8 calls/3,696 generated tokens, with one compiling child. Applicability failures
  remained; richer prompts alone have not established general convergence.
- `__cosf` changed from 28.173 to 28.569 weighted similarity and 111 to 110
  candidate instructions, but positional byte mismatches increased from 362 to
  367. This is not an unqualified byte-exactness improvement, nor semantic proof.
- `__osPopThread` is newly admitted from a ROM-verified four-instruction slice.
  Its best C remains non-exact. `__osGetSR` is parked on `mfc0`, word `40026000`,
  at ROM offset 700416. No assembly fallback is credited as recovered C.
- The two previously exact wrappers reverified and were automatically integrated
  into isolated full-game builds. The complete 8,388,608-byte ROM matched. This
  demonstrates integration of those candidates with the existing game, not
  autonomous recovery of the rest of the reference project.

The checkpoint for that earlier code revision is `eval/results/completion-campaign-mixed-v5.json`.
Its integration receipt, persistent build log and rebuilt ROM are in
`eval/results/completion-campaign-mixed-v5-artifacts/`. The independently rehashed
archived ROM has SHA-256
`58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c`.
The working game source tree was not edited. That revision's regression suite
was 1,053 passed, 9 skipped. Fork old checkpoints after a code change; do not
resume them under changed solver pins.

## Compiler-recipe follow-up and next bottleneck

- Correcting the recipe with identical `__cosf` C reduced candidate instructions
  from 110 to 92 and positional byte mismatches from 367 to 290. Candidate text
  now has the target's 368-byte length; it is still not exact.
- A subsequent bounded model pass improved `probeControllerPak` weighted
  similarity from 92.831 to 96.127. `serviceRumbleMotorRequest` remained 95.489.
  The assembly-origin `__osPopThread` candidate worsened under neighboring C
  settings; it needs explicit compiler-profile search, not a blanket override.
- A fresh three-function SDK development panel produced exact-object candidates
  for `osSpTaskYield` and `_freePVoice` with **zero LLM calls**. Both replacements
  passed an isolated full-ROM build. This is header-assisted m2c intake, not a
  binary-only or heldout benchmark, nor paired proof of recipe causality.
- `osSpTaskStartGo` now matches all 16 instructions and relocation expressions,
  but its strict object certificate correctly remains false: candidate text is
  64 bytes, target text is 80. Target assembly includes three zero words after
  `endlabel`; standalone assembly adds another alignment word. The new scoped
  certificate and isolated integration resolved this plateau without changing C.

Current transfer checkpoint: `eval/results/compiler-recipe-transfer-v5.json`;
receipts, persistent integration build log and rebuilt ROM are under its sibling
`compiler-recipe-transfer-v5-artifacts/` directory. Regression suite:
**1,079 passed, 9 skipped**. No reference function bodies were supplied to repair.

### Function extent versus object layout

`solver.function_boundary` provides a separate, narrow ROM-backed certificate:

- DB address/size, assembly `glabel`/`endlabel`, and both ELF function symbols
  must agree on the extent. Address/word annotations must match the mapped ROM.
- Every byte inside the function and every relocation expression must match.
  External `R_MIPS_26` calls are resolved through pinned linker symbols and the
  resulting linked function bytes must equal the ROM slice.
- The initial backend supports only one text function, no allocated data/BSS,
  zero-only trailing words, and explained section alignment. Other relocation
  kinds, nonzero tails, missing sizes and ambiguous mappings are refused.
- This **does not change** the strict object certificate or `Attempt.exact`.
  It produces `function_exact_pending_integration`, excluded from further C
  repair. No candidate is padded, no target is trimmed, and no binary is patched.
- Before isolated staging, integration replays the boundary check and validates
  source/object/ROM hashes and attempt lineage. Only a successful full-ROM build
  changes the campaign status to `integrated`. With no integration requested,
  these nodes remain `awaiting_integration`, not completed or semantically proved.

The unchanged `osSpTaskStartGo` candidate passed this path and the entire
8,388,608-byte ROM matched with all three SDK panel replacements. The original
seven-function cohort was also reverified under the final code at
`eval/results/completion-campaign-boundary-v1.json`: two existing exacts remain
exact, four nonexact candidates remain nonexact, and the hardware case stays
parked. All runs this boundary revision used zero model calls. This demonstrates
the narrow padding case, not arbitrary boundary recovery or whole-game autonomy.

Next priorities toward autonomy:

1. Extend ROM-backed boundary/relocation checks only when new cases demonstrate
   the need; the first text-only external-call case now works end to end.
2. Support remaining compiler ABI/postprocessors and explicit assembly-origin
   profile search; retain unsupported hardware cases as honest blockers.
3. Run a frozen larger wavefront, route failures by measured class, and validate
   each reusable fix on untouched functions. Improve edit application where the
   model diagnoses a cause but fails to produce compiling C.
4. Recover shared types/data/link layout from binary evidence to remove the
   present dependence on reference headers/build configuration, then validate a
   complete independently reconstructed build.

The transfer cohort is integrated. This command verifies the checkpoint remains
resumable; it does not launch a larger cohort or retry completed functions:

```bash
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state eval/results/compiler-recipe-transfer-v5.json \
  --resume --max-work-items 3 --model-calls 0 --integrate
```

## Larger development wavefront: September 5 follow-up

Selection is frozen in `eval/sets/autonomy_wavefront_dev_24_v1.json`: four functions
per game/SDK and <=24/25-64/65-160-instruction stratum, selected by a fixed hash
order, with zero prior attempt rows at selection and existing heldout functions
excluded. This is header-assisted development, not a heldout or binary-only
capability estimate. The v3 run restarted from m2c/context, while v2/v4 explicitly
replayed historical seeds from the preceding development run.

The initial intake found 1 strict object match, 6 additional candidates with
identical section bytes but different independent relocation-group order,
3 compiling instruction mismatches, 11 noncompiling drafts and 3 unsupported
hardware functions. Compiler-profile mismatch was not the dominant observed
ordinary-C failure in this panel.

Changes tested:

- The object verifier now compares disjoint external scalar relocations and
  intact adjacent HI16/LO16 pairs as groups. It retains raw relocation tables,
  preserves pairing and symbol identity, and rejects normalization for orphan
  LO16s, multi-HI extensions, overlapping writes or unsupported groups. This is
  based on the [MIPS ABI pairing rule and GNU extension distinction](https://sourceware.org/pipermail/binutils/2023-February/125959.html),
  not a blanket relocation sort. Section bytes/layout must still match exactly.
- Existing header preflight/reconciliation runs on context-generated drafts too.
  Exact but integration-ineligible drafts no longer preempt an exact, narrower
  candidate before it is compiled and reverified.
- Per-candidate integration preflight prevents one shared-declaration blocker
  from blocking unrelated replacements. A failed build batch is bisected;
  passing survivors must then pass a combined full-ROM build. Operational errors
  and explicit STOP requests halt that isolation pass.
- Noncompiling drafts skip instruction-only rewrite profiles, which cannot
  operate without an object. They remain eligible for source/model repair.

Frozen v4 checkpoint: `eval/results/autonomy-wavefront-24-v4.json`. Results:

| Outcome | Functions |
|---|---:|
| Object-exact, including integrated candidates | 7 |
| Integrated together into an exact full ROM | 4 |
| Object-exact but integration blocked | 3 |
| Compiling but instruction-nonexact | 3 |
| Noncompiling best draft | 11 |
| Explicit hardware blockers | 3 |

The four integrated functions are `drawRaceTypeSelectCornerSprites`,
`initCharacterSelectCoursePreviewPanel6`, `initCharacterSelectCourseStatsBadge`,
and `waitRaceIntroFlyoverShortPanFinal`. The combined receipt is
`eval/results/autonomy-wavefront-24-v4-artifacts/1788585561616968327-integration.json`.
Its archived 8,388,608-byte rebuilt ROM was independently rehashed and equals
the reference SHA-256 recorded above. Remaining reference game code/assets are
still used in these integration tests; the whole game was not recovered here.

`__osPiCreateAccessQueue` and `waitForControllerPakReplaySaveMessageSecondPage`
need shared-declaration integration. `fadeInEndingCreditsFlow` is object-exact
under IDO, but the real build's stricter C checker rejects six callback pointer
arguments. No failed candidate was put into the working game source tree.

Four repair visits used three GPT-OSS calls and three deterministic compiling
children. `guPerspective` and `__osPfsSelectBank` received applicable edits but
remained noncompiling. `updateRacePlayerMode07LaunchRampPose` remained 98.393.
The popup proposal was rejected by an include-anchor false positive. These
bounded visits do not demonstrate exhaustion of model capability; no semantic
pass claim is made for the nonexact candidates.

After freezing v4, the include guard was corrected: an edit may preserve an
existing include while inserting declarations after it, but may not add,
remove, duplicate or change include context. Substring edits inside includes
are checked against the resulting complete source. Assembly/pragma escapes
remain forbidden. The same popup proposal was replayed with **zero new model
calls**: it now applies but still fails compilation (missing type alias and
incorrect proposed layout). Receipt: `eval/results/include-guard-replay-v1.json`.
Final regression suite: **1,088 passed, 9 skipped**.

Because this last guard fix changed code pins, fork v4 rather than resuming it:

```bash
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state eval/results/autonomy-wavefront-24-v5.json \
  --fork-from eval/results/autonomy-wavefront-24-v4.json \
  --max-work-items 32 --model-calls 1 --timeout 300 --num-predict 6000 --integrate
```

Next concrete priorities: expose the real project's C-checker diagnostics before
integration; preserve useful header/type-context branches across failed-draft
selection; feed source-bound missing-type/ABI/bitcast obligations to repair;
and add conservative shared-declaration integration. The hardware and special
compiler-profile backends remain separate work, not explanations for these
ordinary-C frontend failures.

## Strict frontend feedback and retained drafts (September 5 continuation)

The repair loop now runs the project's default strict clang C policy after
text conversion, on the candidate and its included headers. This does not
import reference translation-unit bodies. The assignment-only Makefile
projection preserves the selected pointer/prototype/return error classes;
unknown policies and missing checkers become explicit operational blockers.
Checker identity is recorded and pinned alongside the compiler inputs.

Object exactness is unchanged. `Attempt.exact` / residual `exact` still describe
the object certificate; `repair_complete` additionally requires a configured
frontend pass. The model receives the source-bound frontend diagnostics even
when object exactness is already true. An isolated frontend pass is not a
full-TU, semantic, portable-C, or whole-ROM certificate.

Intake retains up to three distinct candidate receipts rather than discarding
all but the best failed draft. Search uses fewer m2c unknown types and richer
included-header context as weak ordering hints, never correctness evidence.
Forks carry bounded alternate source identities and recompile them with their
actual attempt parents. Both successful and failed experiments remain logged.

Measured checks:

- Rechecking `fadeInEndingCreditsFlow` reproduced the same six callback type
  errors as its failed full build. The earlier integrated drawing control
  passed. GPT-OSS then repaired the six calls in two bounded model calls,
  preserving the exact object and passing clang. Receipt:
  `eval/results/frontend-repair-fade-v1.json`.
- That candidate plus the earlier four replacements built an exact full
  8,388,608-byte ROM. Receipt and archived ROM:
  `eval/results/frontend-five-v1-integration.json` and
  `eval/results/frontend-five-v1-integration.rebuilt.z64`.
- The same 24-function development panel was rerun from m2c in v5, followed by
  four repair visits / six model calls. `guPerspective` changed from
  noncompiling to object-exact and passed combined ROM integration with the
  previous four. The popup and bank-selection functions became compiling
  candidates with weighted scores 98.409 and 76.250 respectively. These scores
  are not percentages of bytes or semantic correctness.
- The popup's current positional text distance is seven bytes; its first
  mismatches are register choices. Bank selection still has stack-frame,
  instruction-count, and control-flow differences. Neither has semantic
  validation from this compiler-first run.

The audit caught another selection failure in v5: an isolated-pass draft with
extra callback declarations displaced an equally object-exact real-header
draft whose type errors still needed repair. Integration correctly rejected
the former. Intake now prefers the integration-eligible exact source shape
and prevents the extra-declaration version from terminating that frontier.
A regression test covers this case; the full suite is **1,101 passed, 9 skipped**.
The measured v5 checkpoint is preserved, not relabeled after the fix. v6 is an
explicit fork retaining and re-verifying its candidates under the corrected
policy, not a clean or heldout replay.

Final checkpoint: `eval/results/autonomy-wavefront-24-v6.json` (budget-paused,
no background worker left running). Its six bounded repair visits used six
model calls plus the three scheduled deterministic searches. The scheduler
itself repaired `fadeInEndingCreditsFlow` in two calls, without importing the
standalone repair's source. `alSynSetFXMix` also became compiling, score 41.021.
The unchanged-input pins were independently rechecked after the run.

| Cohort outcome | Before (v4) | Now (v6) |
|---|---:|---:|
| Object-exact, including integrated | 7 | 8 |
| Integrated together into an exact full ROM | 4 | 6 |
| Compiling, nonexact | 3 | 6 |
| Noncompiling best candidate | 11 | 7 |
| Hardware parked | 3 | 3 |

The two integration-only blockers remain `__osPiCreateAccessQueue` and
`waitForControllerPakReplaySaveMessageSecondPage` (shared declarations).
The six combined replacements are the previous four plus `guPerspective` and
`fadeInEndingCreditsFlow`. Their final receipt is
`eval/results/autonomy-wavefront-24-v6-artifacts/1788587727884402203-integration.json`.
The archived `.rebuilt.z64` was independently compared byte-for-byte to the
reference: 8,388,608 bytes, SHA-256
`58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c`.
The remaining game code/assets in that build still come from the existing
reference project; this is not an autonomous all-game decompilation claim.

One concrete remaining actuation failure: `updateEndingTommyWaitThenFinalPhase`
still needs its incomplete actor type addressed, but both model proposals
named old source spans that were absent. `alSynSetFXMix`'s second proposal named
an ambiguous span. Both were rejected, not silently applied. Thus richer
diagnostics helped several functions, but do not eliminate source-edit
application failures or prove that the remaining functions cannot be solved.

Resume the current frozen fork (provided code/tool inputs stay unchanged):

```bash
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state eval/results/autonomy-wavefront-24-v6.json --resume \
  --max-work-items 12 --model-calls 2 --timeout 300 --num-predict 6000 --integrate
```

Next engineering targets: conservative shared-declaration integration, and
source-bound field/layout repairs whose patches refer to actual candidate
spans. The compile-success/semantic-validation distinction remains explicit;
no semantic-pass claim is made for these nonexact candidates.

## September 5 continuation: compile recovery and operational inventory

Start future pipeline work with `PIPELINE_MAP.md`. It identifies active and
alternate controllers, mechanism owners, actual wiring, verification gates,
and known limits. `CLAUDE.md` now points to it before new pipeline work. Update
the map and attach a motivating receipt before adding another repair layer.

Implemented and wired into the completion campaign:

- Compatible SDK/header recovery; macro invocations are not prototypes.
- Existing binary-cited global declarations, narrowly filtered to missing
  identifiers and never overriding included declarations.
- Padded opaque-tag completion using existing CFG dataflow and struct rendering.
  These layouts remain candidate hypotheses, not new KB evidence.
- Read-only storage/access/call/type/prototype input for compile repair.
- Physical-line and declaration insertion slots bound to the exact source
  parent; safe normalization of accidentally double-escaped code newlines.
- Actual frontend error ranking ahead of weak header-count hints; existing
  safe do-loop lowering wins ties over helper-blocked drafts. Failed-edit
  history includes frontend diagnostics, not only the helper's first error.
- A compile-only sweep with per-function coverage. Workers stop when both the
  native compiler and configured frontend accept the candidate. This does not
  set object exactness. An unavailable frontend cannot count as a completed sweep.

The original seven were not seven exhausted model failures: six had no logged
model proposals, and the Tommy function's two proposals targeted a read-only
header declaration absent from the editable candidate.

### Measured development replay

Explicit frozen forks: `autonomy-wavefront-24-v7.json`, `-v8.json`, and `-v9.json`
under `eval/results/`. All retain the same 24-function DEV panel and logged
historical source parents. This is header-assisted engineering data, not a
clean unseen evaluation. No reference function bodies were supplied to repair.

| Original compile failure | v9 outcome | Weighted score | Best attempt |
|---|---|---:|---:|
| `updateEndingTommyWaitThenFinalPhase` | Compiles; padded opaque actor tag | 99.744 | 29485 |
| `drawMultiplayerRaceHud` | Compiles; missing cited globals | 79.527 | 29505 |
| `osSetEventMesg` | Compiles; compatible internal SDK headers | 72.600 | 29507 |
| `drawRaceSetupSaveChoicePrompts` | Compiles; globals plus local typed view | 81.513 | 29506 |
| `osPfsRepairId` | Compiles; typed storage, byte-copy accesses, actual field names | 88.425 | 29515 |
| `osPfsIsPlug` | Compiles; loop lowering and stack status array | 91.241 | 29522 |
| `alLoadParam` | Still noncompiling | 0 | 29535 |

The first three required zero model calls to become compiling. The later
repairs used GPT-OSS through the campaign, not hand-edited target answers.
Across v7/v8/v9 there were 22/21/11 provider calls respectively, including invalid
and incomplete output. These are total engineering-run calls, not per-function
first-shot costs. v7 exposed long-slot/escaped-newline failures; v8 recovered
two more functions; v9 recovered the sixth in three model calls with no invalid
proposals and stopped at compilation.

Final v9 cohort: **8 object-exact, 12 compiling nonexact, 1 noncompiling,
3 hardware parked**. No new exact match or ROM integration is claimed here.
The prior six-function combined exact-ROM receipt remains the v6 result;
integration was not rerun in these compile-only forks.

`alLoadParam` still needs coordinated type reconstruction: its m2c draft has a
`void` return conflicting with the existing `s32` prototype, and nested `void *`
values used as structs. Low-effort proposals cycled through incompatible
signatures/macros; the high-effort profile produced an incomplete response then
an ambiguous two-occurrence edit. A bounded profile ending is not evidence
that the function cannot be solved. Source slots exist, but the model does not
always use them. Error counts are also capped by frontend diagnostic limits;
they are not a monotone measure of type-reconstruction progress.

### Independent differential audit

The existing DAG census was invoked separately, without generation. Frozen
manifests and full receipts are `eval/results/compile-recovery-semantic-audit-`
`v1-manifest.json`, `v1.json`, `v2-manifest.json`, `v2.json`, and `v3.json`.

| Function | Final sampled cases | Target instructions visited | Remaining boundary |
|---|---:|---:|---|
| `osSetEventMesg` | 3/3 pass | 25/25 | Opaque interrupt-control callees |
| Tommy actor | 8/8 pass | 39/39 | One unresolved branch edge; callees not fully executed |
| Multiplayer HUD | 2/2 pass | 129/129 | Opaque drawing callees |
| Save-choice prompts | 1/1 pass | 29/149 | 21 unresolved branch edges |
| `osPfsRepairId` | 7/7 pass | 31/150 | 21 unresolved branch edges |
| `osPfsIsPlug` | 7/7 pass | 98/103 | One unresolved branch edge |

`osPfsIsPlug` initially had 3 passing cases and one candidate step-limit result
at 2,000 instructions. Repeating the census at 20,000 produced 7 passing cases;
the original receipt is preserved. That timeout was not sufficient evidence
of semantic disagreement. All these non-leaf results remain provisional with
opaque calls. **28 sampled passes are not an all-input equivalence proof.**
No function received an authoritative semantic-pass designation from this audit.
The audit's `v1`, `v2`, and `v3` lineage checks were clean: all attempts have true
parents, receipt names match the DB, and heldout overlap is empty.

Verification: **1,116 tests passed, 9 skipped**. Frozen code, headers, compiler,
ROM, and reference source hashes were independently rechecked unchanged after
the run. No reference game source/header edits or evidence/type promotions were
made. No background worker remains.

Final checkpoint is `eval/results/autonomy-wavefront-24-v9.json`, status
`compile_sweep_stalled`. Repeating the same resume command will not create a
new strategy for the unchanged remaining source. Next useful work is a bounded
typed-local-view transaction for `alLoadParam`, with unambiguous source-slot
actuation and preservation of the public ABI. Broader semantic/callee coverage
and byte polishing belong to their separate lanes, not to compile-success
accounting. Changed code requires a new explicit fork from v9.

## Coordinated type transactions: implementation and v10/v11 replay

The follow-up request was to implement the machinery needed for a connected
type repair, not to hand-write the target answer. The ordinary repair protocol
remains four edits. New `solver/type_transaction.py` extends the existing
`modelrepair` kernel through an explicit campaign `typed_transaction` profile:

- Up to 64 source-bound slot/new edits applied atomically, with 12,000 total
  old-plus-new span characters and 4,000 net growth. Ambiguous substring edits
  are not accepted in this mode. Include/assembly/overlap/stale-source guards
  remain in force.
- A conservative ordinary public-signature spelling lock, ignoring parameter
  names but not return/parameter types. Missing, complex, or conflicting
  header signatures are explicitly unlocked/ambiguous and remain subject to
  the compiler; this is not an arbitrary C type-equivalence checker.
- Reuses included-header type context and existing CFG/dataflow. Adds an index
  of candidate member chains, assignment dependencies, and return statements.
  Target return-register observations account for the delay slot and preserve
  `unresolved` rather than inventing return values.
- Valid noncompiling candidates that temporarily worsen the ranking can receive
  two follow-up depths while the best candidate is preserved. Their attempts
  retain actual parent edges, including when their error count increases.
  An exploratory child with remaining follow-up can occupy one saved frontier
  slot. No unbounded search or guaranteed eventual solution is implied.
- Invalid output no longer ends this mode immediately while depth/call budget
  remains. Truncation, correction, and generation calls all share that budget.
- Following the first live replay, guards reject recognized new typedef
  collisions with included headers and changes to the control-keyword sequence.
  A type edit may retype a condition, but cannot remove an if/switch/loop line.
  Preprocessor changes remain forbidden. These are scoped checks, not an AST
  or semantic proof.

Wiring: the completion campaign prioritizes this profile for repeated
`member reference base type 'void'` diagnostics, without naming a particular
function. It is otherwise available for noncompiling roots. `eval.agentrepair`
also exposes `--type-transaction` and `--compile-only` for standalone experiments.
All profiles retain ordinary compiler/frontend/oracle verification. There were
no reference-body inputs, target source/header edits, or KB evidence promotions.

### Measured outcome, not inferred from tests

v10 explicitly forked the same 24-function DEV cohort from v9. Its six-call
type-repair visit emitted four applied candidates and two invalid overlapping
edits. Proposal 1646 contained **31 slot edits**, demonstrating that the wider
atomic protocol actually ran. It still chose incorrect types, redefined
`ALFilter`/`Acmd`, and overwrote control lines. No candidate compiled. The
guards added afterward now reject that saved proposal before compilation;
the original receipt is preserved unchanged.

v11 explicitly forked from v10 after those guard changes. Its two-call visit
used the same profile. Both proposals were rejected for redeclaring existing
header types (`ALFilter`, then `Acmd`); no child compilation was performed for
those rejected proposals. Receipt:
`eval/results/autonomy-wavefront-24-v11-artifacts/1788627523220098073-alLoadParam.repair.json`.

**`alLoadParam` remains noncompiling.** The cohort remains 8 object-exact,
12 compiling nonexact, 1 noncompiling, and 3 hardware parked. No new semantic
pass, byte match, or integration is claimed. The larger edit protocol and
bounded lookahead are implemented; successful connected type reconstruction
by GPT-OSS has not been demonstrated on this function. Existing header context
already included `ALLoadFilter`; its presence did not make the model select
and apply that layout correctly.

Tests: **1,127 passed, 9 skipped**. New tests cover multi-site atomicity,
source binding, signature/macro/header/control guards, connected use indexing,
delay-slot-aware return observations, bounded error-increasing lookahead with
true parents, best-candidate retention, and continued budget use after invalid
output. The temporary-error lookahead's success is a synthetic controller test,
not a new live function solve.

Final checkpoint: `eval/results/autonomy-wavefront-24-v11.json`, budget-paused.
Other ordinary repair profiles remain eligible; repeating them is not a new
type-reconstruction mechanism. No worker is left running. Frozen inputs were
rechecked unchanged after this replay. For an unchanged-input continuation:

```bash
python3 -m eval.completion_campaign \
  --repo /home/grant/decomp/sbk1 --db /home/grant/decomp/kb-sbk1.sqlite \
  --state eval/results/autonomy-wavefront-24-v11.json --resume \
  --compile-sweep --max-work-items 1 --model-calls 2 \
  --timeout 420 --num-predict 10000
```

The next materially different experiment should test explicit source-use to
header-field/layout matching against target offsets, using the existing type
and layout tools first. More unstructured header context or simply raising the
edit count again is not supported by these receipts.
## Assisted last-function experiment — 2026-09-05

`alLoadParam` now has an IDO-compiling and frontend-passing candidate. Source
attempt **29647**, fresh selected reverify **29649**, is saved as
`eval/results/alLoadParam-hints-selected-v1.best.c` with its same-stem JSON receipt.
It passes **580/580** target-derived differential cases, including v0; all modeled
reachable instructions and feasible conditional edges are covered in that panel.
`alCopy` remains opaque and finite tests do not prove universal equivalence.

This is **supervisor-assisted development**, not an autonomous or object-exact
solve. The selected weighted score is **78.621**, not an equal-byte percentage.
All seven original compile failures now have compiling candidates available, but
the v11 campaign checkpoint is unchanged; this selected candidate has not been
adopted into it. No integration, game-source changes, or KB promotions occurred.

The complete input ladder, paired controls, provenance, replay commands, and
remaining wiring gaps are in `eval/experiments/alLoadParam-hints/README.md`.
No reference implementation body was inspected. The supervisor supplied typed
declarations and a header-verified field map, and restored the target TU's
existing scoped legacy-return diagnostic metadata. The latter exposed an ABI
parser bug, now fixed with regression tests. Full suite: **1,128 passed, 9 skipped**.

The decisive final syntax comparison: a precise C89 explanation preserved
input-dependent assignments (532/580 passes before return repair); without that
hint OSS forced selector variables to zero (52/580). The already-existing C89
normalizer also compiled the typed source with zero model calls; it is not yet
connected to this controller. A branch-local v0 counterexample then led OSS to
the 580/580 child, but byte score fell 80.259 to 78.621, so the byte-ranked
controller kept the worse-behaved parent as `best`. The selected export explicitly
preserves the passing child from its DB/frontier, without relabeling that outcome.

The ABI parser code changed: the historical v11 resume command above is now stale
for current code pins. Use a new explicit campaign with the assisted source seed
if adopting this result; do not resume v11 or rewrite its frozen receipts.

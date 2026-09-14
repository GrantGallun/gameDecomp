# Differential function debugger

## Purpose

The debugger is the behavioral oracle missing from the logic-first lane. It
runs target MIPS and candidate C compiled to MIPS from an identical register
and big-endian memory image, then compares caller-visible behavior. It does not
use the completed target C body and it does not replace the exact object oracle.

The first pilot is intentionally narrow. `solver/mips_differential.py` is a
dependency-free interpreter for the integer instruction subset used by
`updateRacePlayerMode16AerialTrick`. External calls are intercepted after their
delay slots. The runner records normalized arguments, deterministic results,
the persistent-memory digest visible to the callee, persistent writes, final
memory, declared return registers, ABI preservation, and dynamic instruction
counts.

## Verdict policy

| Verdict | Meaning |
|---|---|
| `passed` | Calls, call-time memory, final non-stack memory, declared returns, and callee-saved ABI agree for this finite test case. |
| `failed` | Target returned normally and at least one candidate observable disagreed. |
| `inconclusive` | The target input was invalid for the harness or either side required unsupported execution semantics. |

Instruction count is reported as a path-specific runtime proxy but is not an
equivalence gate. Emulator wall-clock time is not N64 runtime.

## Grounded pilot

The pilot compares the normalized target object against two arms over three
boundary-shaped states:

1. the same target machine code in independently formatted raw assembly; and
2. the frozen v5 leave-one-TU-out candidate previously rewarded by
   `logic.quality_key` despite its source-level semantic errors.

Run it inside the decomp workspace environment:

```bash
cd /mnt/c/Code/gameDecomp
. /home/grant/decomp/sbk1/.venv/bin/activate
python -m eval.differential_pilot \
  --repo /home/grant/decomp/sbk1 \
  --candidate-tag updateRacePlayerMode16AerialTrick_logic_ref_tu_out_1_1788377198458327152 \
  --expected-source-sha256 d449a4966a2d961e9a00209f64ccf9479dd182b0d91dd8ee43f26f1b19b6dd16 \
  --output /mnt/c/Code/gameDecomp/eval/results/differential-mode16-pilot-v1.json
```

The control passed 3/3 cases. The candidate failed 3/3. On every path, its
first divergence was the call

```text
target:    clampRacePlayerVectorXZSpeed(player+0x40, player)
candidate: clampRacePlayerVectorXZSpeed(player+0x1c, player)
```

Final persistent memory also differed in all three cases. No execution was
unsupported and neither side violated the callee-saved ABI. The complete
machine-readable evidence is in
`eval/results/differential-mode16-pilot-v1.json`.

## Initial pilot boundary (historical)

Later sections document expansions beyond this initial integer-only pilot.

- Only an integer MIPS subset is implemented; there is no FPU, HI/LO,
  unaligned access, indirect-call, or full system support yet.
- Callee arity is supplied by a verified contract table.
- Opaque calls currently receive deterministic register clobbers and return
  values but no memory side effects. This is enough to catch the pilot's first
  divergence, not enough to certify functions whose behavior depends on callee
  mutation.
- Test states are synthetic. Captured game snapshots and coverage-guided state
  generation are still needed.
- Passing finite tests is evidence, never a universal equivalence proof.

The next trustworthy expansion is to add deterministic callee side-effect
models or recursive exact-callee execution, then replay one byte-exact control,
one known semantic mismatch, and one candidate that differs only in source
shape. Only after those controls pass should differential results affect solver
promotion or model feedback automatically.

## GPT-OSS feedback pilot

`eval/differential_repair_pilot.py` fed the three case-specific first
divergences back to `gpt-oss:20b` as bounded JSON-edit requests. Every child was
compiled and re-executed; differential behavior led selection and the object
score was only the final tie-break.

| Round | Model action | Verified result |
|---|---|---|
| 1 | Changed the clamp argument from `player+0x1c` to `player` | Compiled, but behavioral prefix did not improve; rejected. |
| 2 | Identified `player+0x40` correctly | Used invalid arithmetic on `void *`; did not compile. |
| 3 | Emitted a valid cast producing `player+0x40` | Compiled and accepted; summed matching call-argument prefix rose 10 to 16. |
| 4 | Misread the remaining write and changed subtraction to addition | Compiled, but behavioral key and object score regressed; rejected. |

The best child moved the weighted progress score from 90.558 to 90.606 and
corrected the first call argument, but passed 0/3 complete semantic cases and
was not byte exact. Its earliest remaining difference is a target write to
`player+0x44` versus a candidate write to `player+0x20`; 31 final persistent
bytes still differ across the three cases in aggregate. The four generations
used 3,085 tokens. All four proposals, five compiler attempts, five lineage
edges, and four proposal-to-child links are present in the knowledge base.

This confirms that execution feedback can steer GPT-OSS, not that concrete
values are sufficient to finish a function. The next debugger output should
attach value provenance to the first write—for example, which input loads and
operation produced each stored value—and a noncompiling semantic improvement
should receive its compiler error on a repair branch instead of being discarded.
The receipt is `eval/results/differential-repair-mode16-gptoss-v1.json`.

## Full call-trace replay

The debugger now includes the ordered external-call traceback in every repair
observation, rather than reporting only the first differing call. The compact
markers are:

| Marker | Meaning |
|---|---|
| `=` | Callee, normalized arguments, and persistent memory at call entry agree. |
| `~` | The same call and arguments occur, but persistent memory already differs. |
| `!` | The callee or normalized arguments differ. |

This makes later consequences visible in the same observation. For example,
after the clamp pointer was corrected, the trace still showed a later callback
user-ID mismatch even though the earlier calls agreed.

A paired four-round replay used the same frozen source, cases, seeds, model,
and generation settings as the first-divergence pilot. The trace-bearing run
reached the clamp-pointer repair in round 2 instead of round 3, but its final
verified result was unchanged: behavior key `[0, 3, 16, 3, -31, -9, 90.606]`,
0/3 complete semantic cases, and no byte-exact match. GPT-OSS also changed the
player-index macro to offset `0x14`; the target call actually obtains that
argument from `player+0x0`, so the later-call diagnosis did not produce the
right repair.

The richer observation cost 3,964 tokens versus 3,085, an increase of 879
tokens (28.5%). Two harness effects matter when interpreting the result:

- Round 1 contained the correct clamp edit but was discarded because its
  hypothesis exceeded the parser's 400-character limit.
- Round 3 consumed the full 1,400-token completion budget while reasoning
  through the longer trace and emitted no JSON object.

The trace is therefore useful debugger context, but this one replay does not
show better final semantic performance. The next fair test should preserve a
valid edit when only its prose is too long, give trace reasoning an explicit
budget, and send a sliced trace: the matching prefix, first divergence, first
later call mismatch, and value provenance for the earliest divergent write.
The receipt is
`eval/results/differential-repair-mode16-gptoss-trace-v2.json`.

## Causal logic-first to exactness replay

The next controller revision added concrete instruction reads/writes, branch
decisions, load/store provenance, call-argument provenance, aligned persistent
writes, a verified per-case semantic-prefix contract, and separate long
diagnosis and short JSON-patch phases. Patch metadata is salvageable without
relaxing edit bounds, compiler failures receive their own repair branch, and a
whitespace-tolerant fallback accepts only one token-identical source span.

Five mode-16 cases now include two predicate discriminators: sound disabled
with state bit 0 clear, and sound enabled with state bit 0 set. An audit caught
an initial fixture bug: a halfword write at `0x7e` overlapped the intended word
timer at `0x7c`, so neither case reached the callback guard. Removing that
overlap changed the apparent 5/5 result to the correct 3/5 counterexample and a
unit test now verifies the seeded timer and discriminator fields.

The verified progression from the original frozen candidate was:

| Checkpoint | Semantic result | Weighted score | Exact residual |
|---|---:|---:|---:|
| Frozen root | 0/3 | 90.558 | broad logic/dataflow mismatch |
| Causal value/store repair | 0/3 | 89.426 | 8 final-memory bytes remained |
| Original-case pass | 3/3 | 95.625 | false positive exposed by new cases |
| Corrected expanded suite | 3/5 | 95.625 | wrong callback predicate |
| Predicate repaired, v12/v13 | 5/5 | 95.865 | 8 bytes in 5 instruction slots |
| Signed byte repaired, v14 | 5/5 | 97.788 | 7 bytes in 4 instruction slots |
| Signed frame counter repaired, v16 | 5/5 | 99.712 | 6 bytes in 3 instruction slots |

At the 5/5 checkpoint all 27 external calls and arguments, every call-time
memory checkpoint, final persistent memory, and dynamic instruction count
agree. This is finite behavioral evidence, not proof. Exact `.text` comparison
at v12 found 416 bytes on both sides and these eight byte differences:

```text
0x095: 02 -> 0d   0x097: 44 -> 1c
0x099: 0d -> 0e   0x09b: 1c -> 40
0x09d: 0e -> 02   0x09f: 40 -> 44
0x158: 82 -> 92   0x16c: 85 -> 95
```

The last two were target `lb`/`lh` versus candidate `lbu`/`lhu`. GPT-OSS
repaired both from binary residuals, leaving only the first six bytes: one
three-load scheduling permutation. The final blind source is
`eval/results/differential-repair-mode16-gptoss-causal-v18-source-correlated.best.c`
with receipt `differential-repair-mode16-gptoss-causal-v18-source-correlated.json`.

Prompt scope now changes by phase. While logic fails, full static bodies are
omitted and the model sees the matching checkpoint plus the executed causal
windows around the next divergence. Once every semantic case passes, the model
sees current C, only the unresolved exact diff hunk, and generic source-shape
principles. Exactness remains terminal; the controller no longer stops merely
because finite semantic cases pass.

The completed SBK1 source was consulted only after the blind runs as an
open-book audit. It confirms `s8 soundDisabled`, `extern s16 gFrameCounter`,
and the remaining source pattern: an `s32 yVel` local loaded immediately after
the clamp and reused for position Y and `unk74`. GPT-OSS did not synthesize
that local lifetime after multiple 8,000-, 4,000-, and 3,500-token attempts.
Thus the one-function result is strong evidence for causal semantic repair and
partial exactness polish, but negative evidence that unrestricted reasoning
time alone solves compiler scheduling. The next transfer test should freeze
this local-value-web pattern, derive candidates mechanically, and verify it on
unseen functions before promoting it to a permanent repair layer.

## Coverage-guided path enumeration

Semantic agreement is now reported separately from executed-code coverage.
The runner can enumerate structurally reachable instructions and both outcomes
of every conditional branch, collect the instructions and branch edges reached
by concrete cases, and retain boundary-value mutations only when they add new
coverage. Mutations include executed player/global loads, scalar entry
registers, assembly literals and adjacent values, signed/unsigned boundaries,
and deterministic opaque-call return seeds. Any outcome the search cannot hit
is reported as unresolved, not silently labelled infeasible.

On `updateRacePlayerMode16AerialTrick`, the original five-case panel missed two
branch outcomes. Target-led exploration evaluated 487 candidate states and
retained two: an even `gFrameCounter` case and a cleared state-flags case. The
resulting frozen seven-case panel covers all 104/104 structurally reachable
instructions and 18/18 edges across nine conditional branches. The same panel
also covers all 104 candidate instructions and 18 candidate branch edges. The
99.712 candidate passes all seven comparisons with no inconclusive executions.
The audited receipt is
`eval/results/differential-coverage-mode16-v3-frozen-panel.json`.

This closes code/branch coverage only for this isolated function under the
current opaque-call model. It does not enumerate every combination of input
values, prove that apparently unreachable paths are infeasible, or model real
callee side effects. Future functions must earn their own complete coverage
receipt; a partial report must not be described as comprehensive semantic
validation.

## DAG-ordered multi-function census

`eval/dag_pipeline_pilot.py` runs the semantic and exactness gates over a
frozen connected DEV panel in binary-callgraph order, leaves before callers.
It recompiles every frozen root, explores target and candidate paths, compares
observable behavior, and records the first honest stopping stage. It performs
no model generation, so it measures pipeline readiness and routes later repair
work rather than measuring GPT-OSS repair efficacy.

The first audited eight-function run exposed two harness defects: normalized
object dumps can omit a terminal `nop` delay slot, and the synthetic
`gSineTable` region was too small for its 12-bit index. After fixing those and
adding the integer multiply/divide/register-compare instructions required by
the panel, the same frozen roots produced:

| First stop | Functions | Interpretation |
|---|---:|---|
| Byte exactness | 3 | Fully covered leaf behavior passes; source-shape polish remains. |
| Semantic repair | 2 | A concrete observable divergence exists; mode 16 remains provisional because it calls opaque hooks. |
| Target coverage | 3 | Missing branch outcomes or unsupported FPU execution still prevent a complete semantic claim. |

All eight roots compiled and none was byte exact. The three authoritative leaf
passes are `resetRacePlayerTrickSubstate` (5/5), `randomNextMain` (6/6), and
`fixedSine` (7/7). `updateRacePlayerLeanAngle` exposes a wrong first store;
mode 16 exposes the known `player+0x40` versus `player+0x1c` clamp argument.
Modes 37 and 53 execute every target instruction but still miss two and one
conditional outcomes respectively. Mode 40 still cannot execute its FPU path.

The v2 receipt is `eval/results/dag-pipeline-census-v2.json`. Its database
audit reports eight attempts, eight parent links, eight lineage edges, exact
receipt/database name agreement, and no held-out overlap. Passing non-leaf
cases must remain provisional until exact callees execute recursively or have
verified side-effect models.

## Forced-resynchronization multi-divergence replay

The debugger can now expose later candidate faults without pretending that a
bad candidate passed. At the first mismatching aligned external-call checkpoint
or persistent write, it reruns the candidate from the original input, replaces
only that observable checkpoint with the target event, and continues. Up to
three such counterfactual interventions are collected per case and clustered
across cases. Call-entry memory deltas name each differing byte and its last
executed writer. Missing/extra calls and missing/extra writes remain explicit
unsupported control-flow boundaries rather than being silently synthesized.

This mechanism has diagnostic authority only. Acceptance and semantic-pass
status always come from a fresh unintervened execution of the complete
candidate. Unit controls verify that functions with two independent wrong call
arguments or two independent wrong writes produce two clusters while their
ordinary comparisons remain failed.

A zero-token preflight over the five retained failures exposed substantially
more than their first repeated difference:

| Function | Cases | Resynchronized observations | Counterfactual cases reaching the end |
|---|---:|---:|---:|
| `updateRacePlayerLeanAngle` | 7 | 21 | 0 |
| `updateRacePlayerMode40Stun` | 5 | 7 | 0 |
| `updateRacePlayerMode16AerialTrick` | 7 | 12 | 5 |
| `updateRacePlayerMode53AerialTrick` | 13 | 39 | 0 |
| `updateRacePlayerMode37AerialTrick` | 13 | 39 | 0 |

The corresponding three-round GPT-OSS replay used the same model and long
diagnosis/short patch budgets. It completed five functions and fifteen rounds
in 1,557 seconds with 131,526 charged local tokens, zero orchestration errors,
seven compiling candidate rounds, four invalid patch rounds, two accepted
partial changes, zero newly semantic-passing functions, and zero exacts. Lean's
accepted edit reduced aggregate differing persistent bytes from 35 to 23 but
left 0/7 cases passing. Mode 16 only reduced its dynamic instruction-count gap
from 30 to 20, left every observable-prefix component unchanged, remained 0/7,
and fell from 89.788% to 87.933%. Modes 40, 53, and 37 retained their roots.

The failure mode is informative. Modes 53 and 37 consistently showed the wrong
second argument to `updateRacePlayerLeanAngle`, followed by independent clamp
and `fixedSine` argument faults. GPT-OSS converted those concrete symptoms into
unsupported whole-`RacePlayer` layout rewrites; Mode 53 regressed a verified
prefix or failed compilation, while all three Mode 37 patches failed to
compile. More causally independent diffs improved the debugger's map but did
not solve source localization.

Post-hoc comparison with the successful Mode 16 trajectory shows that the
initial interpretation above was too narrow: multiple observables are useful,
but the current packet does not establish that they are independent and does
not bind candidate instructions to exact C expressions. Mode 16 used explicit
raw-offset macros. By contrast, Lean's nominal `unk2F6` member compiles at
`0x2F0`, Mode 37's nominal `unk254` compiles at `0x316`, and Mode 53's local
structure contains overlapping or mislabeled comments that C does not enforce.
The model saw runtime addresses but not those compiler-realized source
bindings.

The governing rule is therefore to retain all divergent sinks but project them
onto a source-linked dependence graph. Cluster sinks by their earliest shared
candidate source/scaffold ancestor, and repair that common cause. Before any
LLM call, verify every accessor's actual offset, width, and signedness through
project headers or a compiler-generated manifest; canonicalize untrusted local
structs to raw-offset accessors. Boundary resynchronization remains useful for
discovering subsequent symptoms, but does not by itself prove causal
independence. For call-rich parents, recursively execute exact callees or apply
verified side-effect summaries before claiming complete semantics.

Receipts:

- `eval/results/differential-wavefront-v5-resync-preflight.json`
- `eval/results/differential-wavefront-v5-resynchronized-failures.json`

The database audit is clean: 19 attempts, 19 lineage edges, all attempts tied
to the correct function, best attempts present, and no held-out overlap.

## Source-linked scaffold experiment: Mode53 exact

The next Mode53 experiment validated the common-cause interpretation. Its
94.634% root did not primarily contain dozens of independent semantic errors:
the invented local `RacePlayer` declaration silently compiled most fields at
different offsets than its own comments claimed. For example, `updateState`
landed at `0x78` instead of target `0x302`, and velocity Y landed at `0x30`
instead of `0x44`.

Replacing that declaration with explicit, binary-observed byte-offset lvalues
while retaining the recovered control flow raised the oracle score to 99.695%.
The remaining 40 sequence differences were six instructions with identical
opcodes, constants, addresses, and order under a consistent register rename.
The two source updates in that block write disjoint ranges (`0x7c..0x7f` and
`0x304..0x305`), so reversing their C order is behavior-preserving. IDO then
assigned the target temporaries and the oracle returned 100.000%, zero
differences, verified exact.

This produced two pre-model gates now implemented in the local pipeline:

1. `solver/source_layout.py` audits simple partial structs under the MIPS-o32
   layout and reports claimed-versus-realized offset cascades in the repair
   prompt.
2. Register-renaming-only exactness residuals activate a bounded deterministic
   search over adjacent statement mutations whose byte ranges are proven
   disjoint; every child still reruns the full semantic suite and exact oracle.

An end-to-end replay through `eval/differential_repair_pilot.py` started from
the 99.695% ordering, compiled three guarded order variants, selected exact
attempt 22728, and terminated before model generation (zero charged tokens and
zero LLM rounds). Receipt:
`eval/results/mode53-deterministic-pipeline-replay.json`.

The result is a confirmed development-function mechanism, not yet a transfer
claim. The next experiment is the frozen same-recipe replay on Lean and Mode37.

## Value-DAG and semantic-stress gradients

The Lean replay showed why branch coverage and load-set alignment cannot be
the only gradients. A seven-case panel covered all 64/64 target instructions
and all 10/10 conditional edges, yet a beam could overfit three cases with
source substitutions that had no plausible C meaning. On a candidate-blind
512-case stress panel, those two apparent 3/7 improvements passed only 207/505
and 196/505 holdout mutations.

The runner now reconstructs the complete dynamic register-definition DAG for
each differing write. Memory leaves record offset, width, signed load opcode,
and whether the value was loaded before or after an earlier write. Alignment
distinguishes entry-versus-memory, wrong binding, stale memory version, and
operation-shape mismatches. Repair packets contain several path-conditioned
trees and their cross-case support counts together with the exact candidate C
def-use lines. A bounded beam may use DAG/load distance to retain a neutral
causal state, but semantic passes remain a separate result and byte score does
not lead selection while behavior fails.

`build_semantic_stress_panel` complements coverage exploration. It mutates
every dynamically observed target load, mutable scalar entry register, and
deterministic filler seed across literal-adjacent and signed/unsigned boundary
values. Selection is round-robin across input dimensions and seed cases; an
input is retained even when it adds no branch coverage. The target alone
selects the panel, so candidate behavior cannot overfit which tests are kept.

This exposed and fixed an emulator error: absolute linker symbols named
`D_HEX` were assigned synthetic data addresses. `D_3FFFF`, explicitly defined
by `undefined_syms_auto.txt` as `0x3FFFF`, is now resolved to that value. The
old behavior corrupted only Lean's `INT_MIN` signed-division correction path.
A regression test compares the relocation form with the equivalent literal.

After the fix, a C hypothesis reconstructed from the operation DAG passed
512/512 cases and scored 80.508%. More importantly, the existing m2c `base.c`
already contained the same correct algorithm. The new zero-model semantic-seed
stage preserves that draft's control/data flow, resolves only linker-backed
absolute unknowns, adds project header context, compiles each adaptation, and
runs the stress panel. On Lean it passed 7/7 frozen and 505/505 holdout cases;
its 31.169% object score is intentionally deferred to the exactness lane.

An equal-input GPT-OSS repair from the older 0/7 C root consumed 48,808 tokens
over four rounds and made no verified improvement (2/64 on the stress subset).
This is negative evidence for asking the model to rediscover whole-function
arithmetic from verbose traces. The routing rule is now: preserve a valid m2c
logic skeleton first; use dynamic DAGs and counterexamples to diagnose its
adaptation; invoke richer model search only when the mechanical skeleton is
absent or demonstrably wrong.

Receipts:

- `eval/results/semantic-stress-lean-beam-v1.json`
- `eval/results/semantic-stress-lean-operation-hypothesis-v4.json`
- `eval/results/m2c-semantic-seed-lean-v1.json`
- `eval/results/differential-repair-lean-operation-gradient-v1.json`

## M2c-first wavefront and predicate gradients

The semantic-seed stage is now part of `eval/differential_wavefront.py`, not a
manually launched side experiment. For each eligible function the controller
first preserves m2c's target-derived control/data flow, resolves explicit
linker constants, adds reconstructed project declarations, and synthesizes
only globals whose width and signedness are supported by binary evidence. A
target-only stress and coverage panel judges the result. A full observed pass
settles the function in the logic lane with zero model tokens; otherwise the
existing differential GPT-OSS repairer remains the fallback. Exactness is
still decided only by object identity.

Two debugger gaps appeared during the transfer run. Mode 40 stopped at
`mtc1 zero,$f4` followed by `swc1`; raw COP1 register/memory bit transfers are
now supported without attempting host floating-point arithmetic. Mode 37
executed every instruction but missed the taken edge of an equality branch.
The explorer now follows the branch operand's trace back to its last target
load and synthesizes equality/inequality or zero/sign boundary inputs. These
predicate mutations run breadth-first across all path-bearing cases before a
broad value sweep, preventing an early seed from consuming the case budget.

The integrated four-function replay produced:

| Function | Differential cases | Target instructions | Target branch edges | Weighted object score | Exact |
|---|---:|---:|---:|---:|---:|
| `updateRacePlayerMode40Stun` | 256/256 | 103/103 | 8/8 | 91.563 | no |
| `updateRacePlayerMode16AerialTrick` | 256/256 | 104/104 | 18/18 | 97.212 | no |
| `updateRacePlayerMode53AerialTrick` | 256/256 | 131/131 | 22/22 | 97.557 | no |
| `updateRacePlayerMode37AerialTrick` | 256/256 | 146/146 | 32/32 | 96.149 | no |

Candidate instruction and branch-edge coverage is complete for all four as
well. This is strong finite semantic evidence, not a proof: these functions
call opaque hooks, so whole-program claims still require recursively executing
verified callees or checking their side-effect summaries. The object scores
are ranking signals, not byte-match percentages, and all four remain nonexact.

Receipt: `eval/results/differential-wavefront-v9-m2c-predicate-gradient.json`.
Its database audit is clean: 21 attempts, 21 lineage edges, every attempt has a
parent, every best attempt is present, and there is no held-out overlap. The
wave used zero charged model tokens and routed all four functions through the
m2c semantic seed rather than GPT-OSS repair.

## Opcode operand ledgers and source-role binding

An audit of later GPT-OSS failures found that opcode visibility alone was not
the main bottleneck. The old causal packets already contained executed target
and candidate MIPS, register values, effective addresses, branch decisions,
and provenance. The model nevertheless confused raw MIPS byte offsets with C
pointer scaling, edited already-correct store operands, hard-coded one case's
value, and often reached the token cap without producing its five requested
conclusions. Some valid local improvements were also discarded by aggregate
trace distance, while downstream-only changes could be retained without
moving the earliest bad observable.

The repair packet now begins with a controller-generated operand ledger. It
names the exact target and candidate store opcodes; marks address, width, and
value independently as exact or different; shows their provenance and the
last selector branch; lists the byte-comparison instruction window; and
summarizes the same first-bad write across all cases. Exact operands are locked
by validation, not merely by prompt wording. While a paired bad write exists,
selection requires progress at that causal frontier. An incomplete repetitive
diagnosis is omitted from the patch phase so it cannot bury the mechanical
facts. Unambiguous address equations can also produce deterministic C
counterfactuals, but compilation and the full differential suite still decide
whether they survive.

`compressRaceRecordReplayData` was the transfer case. A deterministic
nonmutating-displacement candidate first corrected write #2's address. The
cross-case ledger then proved that the encoded length field was one too large
on every failing case. A target opcode/source-role bridge separated attempted
comparisons (`t3`, incremented before byte inequality) from successful bytes
(`a3`, incremented only after equality), and GPT-OSS emitted the coupled C
repair. That advanced the observed suite from 1/4 to 3/4. A final fixed-limit
bridge exposed the candidate-only `a0--`; removing it advanced the function to
4/4 with weighted object score 94.130.

This is an observed semantic pass, not a proof or byte-exact result. Target and
candidate coverage are still incomplete, `semantic_settled` is false, and the
exact oracle remains false. The result demonstrates that the useful input is
not "more assembly" in bulk; it is a compact opcode-to-observable ledger,
cross-case relations, and a mechanically grounded mapping back to editable C.

Receipts:

- `eval/results/differential-wavefront-v52-causal-operand-ranking.json`
- `eval/results/differential-wavefront-v57-counter-role-source-map.json`
- `eval/results/differential-wavefront-v58-fixed-compare-limit.json`

## Exactness handoff audit: normalized source-shape search

The semantic-to-exactness handoff exposed two controller defects on
`compressRaceRecordReplayData`. First, the diagnosis prompt still demanded a
"next behavioral divergence" when all target-observable cases passed, causing
GPT-OSS to infer a false pointer error from physical register names. Second,
the exactness runner still called the old whole-line mutation scanner, so the
new statement normalization never reached the pipeline. A large prologue also
consumed the bounded permutation budget before later independent runs were
sampled.

Exactness now has a separate objective: classify the static residual after
erasing physical register names, preserve all observed values and addresses,
and vary only source shape. Register/local-order residuals reject edits that
change literals or control predicates. The normalized statement generator sees
assignments, compound assignments, increments, and pointer stores regardless
of physical line wrapping, and fairly samples every independent run before
trying deeper relocations.

On the frozen 98.333 weighted-score candidate this exposed 23 deterministic
experiments. Moving the independent `t4++` before `s1++` preserved all four
target-observable cases and raised the weighted object score to 98.478. Four
other cases remain explicitly inconclusive because the target itself reaches
the 10,000-instruction limit; this is not a complete semantic proof. The exact
oracle remains false.

A provenance-tracked beam search then compiled 2,000 descendants through depth
9. None reduced the remaining 15 weighted instruction faults or beat 98.478.
That is negative evidence for further statement-order search on this checkpoint
and points to live-range restructuring as the next exactness lever. A follow-up
experiment inserted one same-typed transparent copy at each of 16 assignment
sites. IDO coalesced every copy: all 16 objects had exactly the same residual as
the root. That candidate generator was removed rather than promoted. A useful
next live-range experiment must actually split/merge lifetimes or scopes, not
add an immediately coalescible copy. No GPT-OSS patch was retained; all repeated
constant-changing pointer proposals were blocked by the exactness-shape guard.

Receipts:

- `eval/results/differential-repair-compress-normalized-v62-connected-order-search.json`
- `eval/results/differential-repair-compress-normalized-v63-live-range-search.json`
- attempt 25817 (verified 98.478 checkpoint)
- run `alloc-order-compressRaceRecordReplayData-1788547001680426891`

## Deterministic exactness gradient and actuation handoff

The remaining 98.478 residual is now classified from the complete reconstructed
instruction streams, not only from changed diff lines. It is
`register-operand-only/full-stream`: erasing physical registers makes the two
streams identical. The controller extracts the bijective allocation cycle
`t3 -> t4 -> t5 -> t3`, aligned target/candidate opcode examples, the first
post-allocation anchor, o32 entry-register-to-C-parameter mapping, lexical
overwrite epochs, and compiler-experiment history. The finished/reference C is
not an input.

This fixed a concrete reasoning failure. GPT-OSS had repeatedly interpreted
physical entry register `a2` as the current C local named `a2`, then proposed
changing `dst + 1` to `dst + 2`. The ABI map shows that physical `a2` is the
third parameter, `dst`; the full-stream classifier proves that no immediate or
pointer displacement differs. Literal and control-predicate changes are now
rejected before compilation.

Same-source experiment history survives process restarts through the attempt
DAG. On the frozen source the feed recovered 64 earlier probes: 38 statement-
order, 16 transparent-copy/materialized-web, and 10 declaration/initializer
experiments, all compiled and none accepted. Their source hashes seed the
deduplication set, so a replay does not recompile them.

A conservative zero-token split-epoch generator was added for scalar epochs
whose defining assignment is inside a structured loop and whose uses remain
inside the defining block. Its motivating candidate split `a3`'s inner-loop
match-length web. The candidate compiled, preserved all four target-observable
cases, and produced the identical 98.478 object residual: 13 register faults
and the same allocation cycle. This is a useful negative result and is now the
65th remembered exactness experiment; it does not establish full semantic
equivalence because four target executions still hit the step limit.

Register-only residuals now bypass the free-form diagnosis call after bounded
deterministic search. A smoke run configured with an 8,000-token diagnosis
budget recorded zero diagnosis tokens and proceeded directly to the constrained
source-shape actuator. GPT-OSS still failed to emit a retained follow-up edit,
but it can no longer spend the diagnosis budget narrating physical register
names or silently repeat previously compiled deterministic probes.

Receipts:

- `eval/results/differential-repair-compress-normalized-v64-exactness-gradient.json`
- `eval/results/differential-repair-compress-normalized-v71-deterministic-epoch-split.json`
- `eval/results/differential-repair-compress-normalized-v72-controller-handoff.json`

## Cross-function compiler-response policy

The rewrite catalogue is an action space, not a learned response model.  The
new `solver/transition_policy.py` reconstructs source-changing parent/child
edges from the attempt DAG and records the compiler response to each coarse
action family.  Its input state contains the residual fault vector, full-stream
classification, register-cycle shape, residual size, and simple source/live-
range shape.  Outcomes include compilation, exactness, Pareto fault-vector
movement, regression, cycle movement, and per-axis reductions.  Byte score is
retained as telemetry but is not reduced to another weighted scalar loss.

Estimates back off from exact context to residual class, dominant fault, and
global family history.  Exact probability is a weakly smoothed empirical rate;
one exact child among a thousand regressions therefore does not become a
Boolean override.  All transitions belonging to the function being ranked are
excluded.  The policy can only reorder a bounded candidate panel: compilation,
the full semantic replay, and the exact-object verifier remain hard gates.  A
wavefront fits this policy once before it creates new attempts and shares the
frozen model across child runs.

The first real fit for the 98.478 `compressRaceRecordReplayData` residual used
1,263 source-changing transitions from 40 other functions (1,270 after the
live experiment below).  Its 21 currently generatable candidates were already
present among 65 remembered experiments, so a rerun would be a deduplicated
no-op.  Cross-function evidence ranks statement order first: three applicable
transitions from one other function include the exact
`swap-independent-statements-74-75` transition on
`updateRacePlayerMode53AerialTrick`.  Split-epoch remains unseen outside the
target; declaration/initializer variants have 23 transitions across two other
functions and no exact result.  This is useful ordering evidence, but its
one-function statement-order support is still thin.

A leave-one-function-out replay over historical sibling panels found 65
multi-family parents, but only four where the conservative fault-vector outcome
made one family better and another worse.  The policy chose a productive
family first on 3/4, versus 2/4 for chronological order; mean first-success
rank was 1.25.  There were no multi-family exact opportunities in that audit.
Those figures are an observational smoke test, not a benchmark: old runs did
not try every family at every parent, and old attempts do not store semantic
status uniformly.

The first live transfer test used `randomNextObject`, whose root already passed
80/80 target-derived semantic cases with complete target and candidate
coverage and scored 98.75 nonexact.  The policy ordered seven novel probes.
All seven compiled, all preserved 80/80 cases, and every one produced the
identical 98.75 object; none was accepted and the run used zero model tokens.
The failed probe families—register qualifiers, declaration/initializer splits,
and increment/value-web spellings—are now reusable negative compiler-response
evidence rather than a discarded per-function debugging session.

Receipts:

- `eval/results/gradient-policy-v1.json`
- `eval/results/gradient-policy-randomNextObject-v1.json`
- `eval/results/differential-repair-randomNextObject-gradient-policy-v1.json`

### First learned exact expression-web transition

`randomNextObject` initially had only two register faults.  Its target and
candidate were the same nine opcodes, offsets, immediates, and relocations; the
old byte loaded from offset 0x518 occupied `t6` in the target and `v1` in the
candidate.  Lexically narrowing the `idx` scope, removing the long-lived
address-only pointer, and materializing that pointer only at its final use all
compiled to the identical 98.75 residual.  Those experiments reject those
specific scope/pointer operationalizations here; they do not imply that scope
or pointer lifetime can never affect IDO elsewhere.

Fusing the complete update into `return table[++field]` did move the compiler
but collapsed old and new byte values into one `v1` web.  The residual grew
from two register faults to five and the score fell to 95.625.  That response
made the missing distinction mechanical: the target has separate old, new,
and post-truncation lookup webs (`t6 -> t7 -> t8`).  The next bounded family
kept those stages while removing only the named old-value local.

The direct form:

```c
arg0->randomIndex++;
return gRandomTable[arg0->randomIndex];
```

compiled byte exact on attempt 27917.  All 80 target-derived cases passed with
complete target/candidate coverage, no model call was made, and the controller
accepted the candidate only after the exact verifier returned true.  The
policy now contains a `direct-byte-update` transition with one exact result;
because it is confirmed on only one function, it remains high-value but thin
transfer evidence rather than a universal compiler rule.  The corresponding
guarded generator requires an unsigned-byte destination and the exact
load/increment/store/immediate-lookup data-flow shape.

A bounded scan of the current unsolved residuals emitted no second candidate
with that guarded source shape.  That is a transfer-availability result, not a
failed replay: the rule remains confirmed on one function until an independent
matching residual enters the wavefront.

Receipts:

- `eval/results/differential-repair-randomNextObject-scope-v1.json`
- `eval/results/differential-repair-randomNextObject-pointer-lifetime-v1.json`
- `eval/results/differential-repair-randomNextObject-expression-fusion-v1.json`
- `eval/results/differential-repair-randomNextObject-three-web-v1.json`

### Leaf replay: alias coverage and complete candidate panels

The five-leaf v59 wave initially settled all five functions semantically but
matched none. Follow-up testing found that `Fendit`'s apparent pass omitted
pointer aliases: when the byte input points at `arg0 + 0xc2`, the candidate
zeroed that byte before reading it. Adding target-write-derived pointer inputs
changed the old candidate to 65/70. Branch coverage alone missed this defect.
The alias mutation feed is now wired into repair, stress, coverage, DAG, m2c
seed, and semantic-beam entry points. It remains bounded testing, not a proof
over all aliases or inputs; the current alias generator targets the player
region's observed write starts and scratch pointer bases.

An early-read temporary restored 70/70. Inlining the read into its destination
store and grouping the returned increment as `*input++` then reached verified
exactness on `Fendit` (27959). The same family transferred to `Fvelocity`
(27965), also exact and 70/70. Both repair sequences used zero model tokens.

On `fixedCosine`, reusing the normalized angle parameter raised the score from
86.375 to 98.75. Naming just the table load reached 99.167. Naming the complete
converted table/shift result reached verified exactness (27979), with 80/80
cases and zero model tokens. These are compiler similarity scores; they are
not literal percentages of identical bytes. The exact flag is authoritative.

This exposed a controller defect: after the first improving candidate, it
discarded untried siblings and expanded only the winner. The exact scalar-result
candidate had already been generated but never compiled. The search now finishes
each bounded sibling panel before expanding its best candidate, stopping early
on exactness. Parent source/attempt lineage stays frozen for that panel, and
accepted sibling artifacts get distinct filenames. A regression test reproduces
the first-improvement/exact-sibling case and checks both parent edges.

The return-load generator also now skips earlier constant returns, preserves
unbraced control flow with a compound statement, and declines conditional or
side-effecting surrounding expressions. Every emitted source still goes through
compilation and ordinary differential replay. The new return grouping is
confirmed on one development function, not established as universal.

Receipts:

- `eval/results/differential-wavefront-v59-learned-exactness-wave.json`
- `eval/results/differential-repair-Fendit-alias-audit-v1.json`
- `eval/results/differential-repair-Fendit-inline-early-store-v1.json`
- `eval/results/differential-repair-Fvelocity-direct-postincrement-v1.json`
- `eval/results/differential-repair-fixedCosine-complete-panel-v4.json`
- `eval/results/differential-repair-calculateRaceTimerDelta-stress-v1.json`

The next retained timer candidate is still nonexact (94.324), but passed 80/80
stress cases. Its two-round v60 model replay produced one no-op patch and one
non-improving edit, with repetitive diagnosis text and 16,547 recorded tokens.
This is evidence of a byte-repair/search bottleneck on this candidate, not a
new observed semantic failure. No finished/reference function C was consulted.

### Switch coverage invalidates the callback's old settlement

The completed v60 replay used the old v32 census and reported both remaining
nodes settled, after 32,904 recorded/charged tokens and no accepted improvement.
Its callback coverage claim is invalid: `_static_coverage_targets` stopped at
every `jr`, including the switch dispatch `jr t8`, so it counted only 15
reachable instructions. Passing those paths did not validate the switch bodies.

Coverage now follows switch destinations recovered from ELF table relocations.
Unresolved register jumps remain explicit coverage debt; they cannot become
returns by omission. The same successor treatment is used for dominance checks
so an indirect branch cannot justify pruning a feasible conditional edge.
Known entry-ra copies into callee-saved registers remain recognized as returns.

The callback's fixed source passed 256/256 stress cases, but corrected target
coverage was only 23/134 instructions (17.2%) and 3/26 conditional edges (11.5%).
The current panel does not establish the allocation/list-insertion paths or
most switch cases. Generating valid pool/list state and switch-index witnesses
is the next work item. This is a coverage/input-generation blocker, with no
observed semantic mismatch yet on the sampled cases.

Coverage reports now carry `coverage_model_version=2`. Wavefront routing marks
old complete reports stale/partial until a new census or current preflight
recomputes them. Old receipts are preserved as historical evidence; v60's
callback settlement must not be quoted as current full-function correctness.

Receipts:

- `eval/results/differential-wavefront-v60-remaining-leaves.json`
- `eval/results/semantic-stress-callback-switch-coverage-v1.json`
- `eval/results/differential-wavefront-v61-coverage-revalidation.json`

### Audit implementation: verification, input search, and faithful patching

The primary workspace oracle now gates `Attempt.exact` on an independent
big-endian ELF32/MIPS certificate, not just the normalized assembly banner.
`solver/byte_certificate.py` compares allocated text/data/BSS size, alignment,
bytes, and ordered relocation expressions. Unsupported artifacts fail closed.
Debug/ABI metadata is explicitly outside this section-comparison scope. The
receipt binds source, object and known build-input hashes, and is persisted
beside the object and in the attempt's sampling metadata. This establishes
equality under the same link environment, NOT a final-ROM checksum or a
hermetic-build certificate. Local-data relocation aliases can conservatively
fail this gate; the existing explicitly placed relocated/data oracle remains
the separate way to investigate those cases. Historical exact rows and build
script archives have NOT all been recertified.

`eval.verify_all_exact` now chooses the latest exact attempt deterministically,
uses distinct artifact names, and exits nonzero on any failed recertification.
It writes build artifacts but does not replace project sources or old DB rows.
The fresh `fixedCosine` certificate (27992) passes, with 80/80 semantic cases
and zero model tokens.

Transition policy v2 ignores unified-diff file headers and hunk locations when
measuring residual movement. Instruction text remains significant. This fixes
the 390 header-only changes found in the audit without altering historical data.

The repair controller now distinguishes an incomplete diagnosis from a
completed hypothesis. Token-limit and thinking-only responses get one bounded,
non-thinking completion call. Both calls and token costs are recorded. If the
completion still lacks substantive ordered conclusions, patch emission is
skipped. Markdown headings and typographic hyphens are accepted. Completion
and patch prompts retain the next-observable operand constraints: fixing a
store's width must not move its already-correct byte address.

Final semantic results now include a candidate-specific certificate containing
source, target/candidate assembly, test-panel, and runner hashes plus the call
contract. Both target and candidate coverage are recomputed from that exact
candidate's runs. Wavefront settlement uses these final coverage reports,
not initial census flags. These are sampled synthetic-environment results;
even complete instruction/branch coverage is NOT universal equivalence.

`eval.coverage_worker` implements the former coverage-only routing placeholder.
It runs target-directed exploration, expands the retained states with value
stress tests, compiles/replays the candidate, and saves reusable selected cases.
The wavefront invokes it before parking a passing-but-partially-covered node.
New concrete failures are forwarded to source repair with the richer panel.
`differential_repair_pilot --panel-receipt <coverage-worker.json>` reuses that
panel, checking its function and target identity. Small relocation-derived
jump tables now contribute every selector to the mutation domain, including
non-power-of-two cases 3 and 5. Uncovered paths remain explicit debt.

The deterministic exactness search now retains a four-entry diverse frontier,
with at most four parent expansions per repair round. The champion is preserved
separately. A lower-scoring, semantically passing source can therefore produce
the next exact candidate instead of being discarded immediately. A regression
test specifically requires this downhill step. This is a bounded deterministic
frontier, not a claim of unrestricted search or unseen-function transfer.

#### New callback evidence and controller defects

On the unchanged callback candidate, coverage increased from 23/134 to 109/134
instructions with 5,000 target-only search probes. Its 256-case panel exposed
43 failures: the target uses `sh` for `isActive` at +0x16, while the candidate
uses `sb`. The selector enumeration then reached 129/134 instructions and
23/26 conditional edges on a different 256-case panel (224 pass, 32 fail).
Those panels differ; their failure counts are not a before/after repair score.
The remaining five instructions are in list traversal.

Model replay found two machinery errors, not merely model errors:

- `source_layout` silently omitted attached pointer declarations and function
  pointers, then emitted wrong offsets as mechanical facts. It now accounts
  for those pointer slots and refuses a layout with any unparsed declaration.
- `c89.hoist_declarations` treated struct members as block-local declarations.
  A correctly emitted `u8` to `u16` field patch was followed by automatic member
  reordering, corrupting the layout. Struct/union/enum bodies are now excluded
  from declaration hoisting, including nested and local aggregates.

All discovery and replay used target assembly/objects, candidate sources, and
project build/header metadata, not the finished reference function C.

Receipts:

- `eval/results/differential-repair-fixedCosine-byte-certificate-v1.json`
- `eval/results/coverage-worker-callback-audit-v2.json`
- `eval/results/coverage-worker-callback-audit-v3.json`
- `eval/results/differential-repair-callback-coverage-audit-v1.json`
- `eval/results/differential-repair-callback-coverage-audit-v2.json`
- `eval/results/differential-repair-callback-coverage-audit-v3.json`

The width-only observable lock now checks fully understood before/after struct
layouts. When a unique field has the observed width/offset, widening it must
preserve its byte offset; a padding edit that moves it is rejected with concrete
computed offsets and sent through the existing patch-retry path. This avoids
turning a solved address into a fresh mismatch while fixing the store width.
Ambiguous layouts are not guessed; compiler/differential replay still decides.

No project source integration or final-ROM claim was made during this audit.
The next evaluation is a frozen unseen-function panel; current live replays are
development/debugging evidence, not an autonomous-transfer benchmark.

Controlled replay of already-recorded GPT-OSS proposal 1502 through the repaired
normalizer produced attempt 28002: 256/256 on the identical v2 panel, versus
213/256 before; compiler similarity 90.291 -> 91.638, still nonexact. No new
model call was used for that replay. This isolates the normalization defect
from sampling variability. Fresh v4/v5 model draws still made bad padding edits;
the new width lock rejects those instead of claiming progress. The repaired
candidate is retained in the attempt DB and in
`eval/results/callback-recorded-patch-normalizer-replay-v1.best.c`, with its
certificate in `eval/results/callback-recorded-patch-normalizer-replay-v1.json`.
The 129/134 coverage result was measured on the later v3 input panel; do not
combine that coverage with the repaired candidate's v2 pass as one certificate.

Validation after implementation: 944 tests passed, 9 skipped. Existing staged
and unstaged user work was preserved; no commit was made.

The local C export adds a final newline to the recorded model source. It was
therefore separately recompiled and replayed, rather than claiming the original
source hash also certifies the export. Its own receipt is
`eval/results/callback-normalizer-replay-export-v1.json`.

### Unattended replay, richer inputs, dependency pins, and integration gate

The next-step review keeps semantic-first repair, but puts validation at each
transformation boundary. An existing correct model edit must survive patching
and normalization before more inference is purchased. Function-level differential
validation is also distinct from whole-program equivalence (see the primary
[D-Helix study](https://www.usenix.org/conference/usenixsecurity24/presentation/zou)).

- The repair worker automatically replays up to eight historical proposal
  variants, raw and C89-normalized, against their original source parents.
  Function identity, parent source hash, held-out protections, current compiler,
  and the current differential panel gate acceptance. Every replay has lineage
  and zero new inference tokens. `--proposal-replay-budget 0` disables this for
  an ablation; `--proposal-cutoff` fixes the available proposal history.
- Coverage mutations now include bounded argument scratch memory (`@arg1+0xe`
  in a case's `global_writes`, likewise arg2/arg3), not only globals/player
  memory. Comparison-directed search can invert `slt`/`sltu` predicates through
  both loaded fields and ABI-declared scalar entry registers. This matters when
  a signed field cannot cross the current scalar argument. The wavefront runs
  this coverage search on incomplete panels even if they already have failures.
- Dependency evidence is fingerprinted. A changed/missing callee queues its
  callers and transitive callers for revalidation and removes settlement.
  Only source-bound, independently byte-certified, nonstale callee ABI contracts
  enter repair prompts. These are not executable side-effect models. Census
  ordering groups strongly connected components instead of rejecting recursion;
  this does not implement joint recursive execution or prove SCC equivalence.
- `python -m eval.frozen_wavefront --repo ... --db ... --census ... --output ...
  --parent-wave ... --functions f g --rounds 1` runs a preselected development
  panel. It pins controller files, target objects/assembly, headers/toolchain,
  historical proposal/attempt cutoffs, compiler-response policy, and local model
  digest. Code/input changes invalidate the receipt. New attempt receipts are
  allowed; editing historical selected-function evidence is not. This wrapper
  does not claim an unseen benchmark or a hermetic OS/container snapshot.
- `python -m eval.integration_gate --repo ... --manifest ... --output ...`
  applies explicitly prepared C/header file replacements to a new disposable
  copy, runs `tools/build-and-verify.sh`, then independently compares the whole
  ROM, including length. It never overwrites the working game repository. The
  manifest binds `reference_rom`, `reference_sha256`, `built_rom`, and each
  replacement's `path`, `base_sha256`, `replacement` (manifest-relative file),
  and `replacement_sha256`. Symlinks fail closed. Build failures retain the
  staging directory/log. Source-to-project packaging remains explicit; this is
  not yet an automatic function-to-translation-unit integrator. ROM equality
  with assembly fallbacks does not establish an all-C decompilation.

Live development evidence: automatic callback replay (receipt
`eval/results/automatic-proposal-recovery-callback-v1.json`) recovers the useful
historical edit without selecting its proposal ID manually: 256/256 on the old
panel, score 91.638, nonexact, zero new inference tokens. The richer scratch v5
panel executes 134/134 target instructions and 25/26 conditional outcomes but
finds 15 failures (241/256 pass) in that candidate. Complete instruction coverage
therefore does not establish correct semantics. The remaining branch outcome
and list-state failures are explicit work, not presumed solved.

The first frozen two-function experiment completed unchanged in 218 seconds,
with 11,525 local inference tokens: timer 256/256 and nonexact; callback 204/256
at entry, automatically recovered to 241/256, then stalled. Both new model
patches were rejected. In particular, the callback diagnosis mistook a different
reaching definition for a compiler relocation bug. Target `lhu` versus candidate
`lh` at the same list-field address changes the insertion predicate. Prompt v52
adds a generic same-address/width/raw-bits load-interpretation report, including
dependent branch operands before the bad observable. It is diagnostic alignment,
not a new success gate. No target-specific C patch is embedded in this module.

Follow-up frozen v2/v3 model trials did not solve the fault: one finished with
unnumbered conclusions rejected by formatting, and the final trial exhausted
diagnosis/completion budgets. Prompt v53 removes repeated observable ledgers,
puts the upstream contrast first (including inside the 12,000-character
completion packet), and accepts complete unnumbered headings. These presentation
changes are not counted as a demonstrated model win.

The narrow deterministic alternative *did* work. An observed same-address
signed/unsigned load contrast now activates at most four explicit matching-width
cast experiments, excluding comments/strings. The ordinary compiler and complete
current differential panel still gate acceptance; the generator contains no
function names, addresses, or finished-reference C. Callback attempt 28039 changes
one cast, passes 256/256 on the identical panel (previously 241/256), and increases
assembly similarity from 91.638 to 92.986, with zero model calls. It is not byte
exact and still has 25/26 target branch outcomes. This is a newly engineered
development-target repair, not evidence of transfer to unseen game functions.
The exported source hash matches its own semantic certificate:
`b1f16f0c9e0610f1d2a0cc5bf59dc68712b4da403d3cd2d2bd0e6d704b35e11a`.
Receipt: `eval/results/deterministic-load-extension-callback-v1.json`.

The frozen wrapper also accepts `--deterministic-only --rounds 1`, exercising
coverage, proposal recovery, and trace-derived experiments without local model
generation. The isolated integration gate has fixture tests only; no game source
replacement or whole-ROM build was performed in this work. Automatic project
packaging, executable callee/SCC models, remaining input coverage, and a genuine
unseen-function benchmark remain open. Validation: 963 passed, 9 skipped.

Final entry-point check: `eval/results/frozen-wavefront-deterministic-control-v4.json`
completed with frozen inputs unchanged in 14.04 seconds. From the previous
241/256 callback root, one deterministic candidate reached 256/256 with zero
model tokens; similarity 92.986, nonexact, partial branch coverage. The wrapper
records observed semantic pass but correctly leaves semantic settlement false.

## Executable leaf calls and stack storage (2026-09-06)

The active completion worker's `eval.semantic_lane.Panel` now loads supported
integer leaf assembly through `solver.callee_execution`. Admission checks the
callee symbol/extraction range against the pinned ROM and reassembles the parsed
instructions to its bytes; names, annotation comments or a prior score cannot
authorize execution by themselves. Relocations, nested calls, hardware, FPU and
computed jumps remain unsupported in this deliberately narrow leaf backend.

Both caller versions execute the same admitted original callee with shared
memory/registers and a shared instruction budget. The internal call's physical
stack address is no longer an opaque observable; its actual reads, writes,
returns and pointer escapes determine caller behavior. Nested trace indices do
not count as caller coverage. ABI faults, frame escapes and uninitialized stack
reads cannot silently fall back to a passing opaque call. Frame bounds are not
all C object/subobject bounds, and this is not joint candidate-callee execution.

An explicit `OutputBuffer` test environment supports one bounded non-escaping,
address-insensitive, write-only output on a specified success result. It is never
auto-imported as binary evidence. Ambiguous stack aliases decline. The current
__osContRamRead environment is an assumption for DEV testing, not a hardware
backend or a proven general side-effect summary.

The companion `stack_buffers` generator proposes address-only byte-local arrays
from unique call bindings and target stack spacing; the resilient worker compiles
and records every candidate. On __osBlockSum, this fixes the one-byte buffer:
59/136 saved-root passes versus 136/136 array passes under identical explicit
output assumptions and real __osSumcalc execution. Combined modeled caller
instruction/branch coverage is complete. Both remain nonexact at score 81.492.
Two other callers retain 64/64 passes with actual alCopy/callback-setter effects.

Without the output assumption, __osBlockSum's success path still encounters an
uninitialized stack read. Exploration now retains unsupported-execution examples
even when target-led case selection excludes those inputs. A passing selected
panel with this debt is `observed_pass_with_execution_debt`, not an unqualified
observed pass. All statuses remain finite, non-authoritative diagnostic evidence.

Receipts, exact source/attempt identities, activation counts and next limits:
`eval/experiments/campaign-gap-audit/README.md`,
`eval/results/stack-callee-replay-v3.json`,
`eval/results/stack-callee-summary-v2.json`.

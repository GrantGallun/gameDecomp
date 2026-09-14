# Token-efficient wavefront methodology

The wavefront is the dependency-ordered execution policy for the decompiler.
The flywheel is its verified memory. In conversation, "waveform" refers to
this same wavefront method; code and receipts use `wavefront` consistently.

The central rule is simple: **spend model tokens only after a deterministic
check proves that new information activates on a concrete unresolved parent.**

```text
exact leaf receipt
        |
        v
ABI/effect contract -----> frozen flywheel node
        |
        v
parent callsite binding (arguments, widths, offsets, return uses)
        |
        v
validate stored parent candidate                0 model tokens
        |
        +-- valid ----------> continue ordinary oracle refinement
        |
        +-- violated -------> one mismatch-only repair call
                                      |
                                      v
                             compile + revalidate
                                      |
                                      +-- exact --> promote parent; unlock callers
                                      +-- not exact --> retain receipt; PARK
```

## Authority layers

Keep these separate in every prompt, receipt, and promotion decision:

1. **Binary facts:** call targets, load/store width and signedness, byte
   offsets, argument bindings, and return consumers. These are mechanical.
2. **Compatible exact-leaf facts:** prototype spelling and effects from a
   candidate already accepted by the target compiler and byte oracle.
3. **Semantic hypotheses:** inferred function names, parameter meanings, and
   field roles. These improve search and readability but are retractable and
   may never override layers 1 or 2.

Original local-variable names are unnecessary. Stable identities such as
`param0`, `ret@0x800580AC`, linker symbol plus byte offset, and compatible
element index are enough to constrain a parent.

## Token ladder

Every experiment climbs this ladder in order and stops as soon as a gate
fails:

| Stage | Model-token budget | Required output |
|---|---:|---|
| Receipt audit | 0 | Prior draws, tokens, wall time, exacts, missing telemetry |
| Prevalence/coverage | 0 | Number of functions on which the mechanism activates |
| Stored-candidate replay | 0 | Compiles, exact verdict, callsite-contract verdict |
| Microprobe/unit test | 0 | Causal compiler or extractor behavior |
| Mismatch-only repair | At most 1,200 generated tokens | One candidate for one activated parent |
| Replication | Explicit new budget | Only after a closure or repeated mechanical gain |

Do not run a multi-arm generation experiment merely to learn that an arm had
no eligible callee, never inspected the supplied context, or contradicted a
fact detectable in its compiled assembly.

## Frontier selection

`eval.wavefront_budget` builds a zero-model-call manifest. A parent is eligible
only when:

- binary and evidence call counts align;
- at least one frozen exact callee is present;
- every exact-callee argument is resolved; and
- the contract contains caller-relevant information.

Before reserving generation tokens, the best stored compiling candidate must
also fail the argument/return-flow validator on a genuine normalized binary
fact. "Information exists" is not activation.

The ready frontier is ordered by:

1. consumed exact-callee returns;
2. caller information score;
3. historical parent closeness; and
4. smaller instruction count.

Build the current frozen plan with:

```text
python -m eval.wavefront_budget \
  --coverage eval/results/callsite_contract_hard_panel.json \
  --generation-receipt eval/results/callsite_contract_generation_two_parent.json \
  --generation-receipt eval/results/callsite_contract_generation_two_parent_v2.json \
  --generation-receipt eval/results/callsite_contract_generation_two_parent_v3.json \
  --generation-token-budget 2400 \
  --repair-token-cap 1200 \
  --max-targets 2 \
  --out eval/results/wavefront_token_budget_plan.json
```

The manifest is a reservation, not an instruction to spend. Each selected
target first undergoes zero-token stored-candidate validation. Unused repair
reservations cost nothing.

## Flywheel promotion and retrieval

A newly exact parent is promoted once, with:

- exact attempt and oracle authority;
- machine profile and call edges;
- compact ABI/effect contract;
- source profile and exact compatible source for audit;
- experiment outcomes, including regressions; and
- content digests for every referenced artifact.

Promotion immediately recomputes which callers are newly informative. Full
callee bodies remain available for audit and high-similarity retrieval, but
the default parent interface is the compact callsite contract. A score or
semantic annotation can never promote a node.

## Experiment discipline

- One variable per causal experiment.
- A detector must demonstrate prevalence before a model run.
- Record activation, not-applicable, compilation, contract validity, exactness,
  generated tokens, prompt-token telemetry, wall time, seed, and artifact hash.
- Artifact names are run-scoped; stale files from an earlier draw are invalid.
- Score ranks candidates. The exact oracle decides promotion.
- DEV chooses policy. Held-out evaluates a frozen policy once.
- A score-only movement without a replicated exact gain remains inconclusive.

## Current evidence and decision

The three callsite-prompt pilots spent 33,614 recorded generation tokens over
36 draws and produced zero exact matches. Their score movements changed sign
between replications, so unconditional prompt injection is not enabled.

The deterministic mechanism did provide value: on `func_8005804C`, the paired
baseline violated both exact-leaf argument contracts, while the guided
candidate and target passed both. Therefore exact-leaf contracts are retained
as validation and repair constraints, not as mandatory prose in every prompt.

The 2,400-token manifest selected `renderRaceUiSingleTrailEffect` and
`func_8005804C`, but its deterministic gate rejected both reservations: their
best stored candidates replayed at 97.532% and 68.536% and already satisfied
every exact-leaf argument and normalized return-flow fact. A scan of the three
eligible deferred parents found two more contract-valid candidates and one
with no replayable stored candidate. WF1 therefore spent zero model tokens and
promoted nothing. This does not refute leaf-first scheduling or verified
cross-function memory; it shows that the current leaf facts have no repair
surface on the current best-parent cohort. The next wavefront repair is allowed
only after a zero-token scan locates a concrete violation.

The ABI-sensitive DEV pilot on 2026-09-01 tested how to create a more
compiler-active leaf pool. After independently excluding every frozen
held-out name, a zero-token scan found 11 transfer-qualified leaves and chose
eight. One seeded draw per leaf cost 2,159 generated tokens and 34.249 seconds,
but produced 0 exact leaves, failing the preregistered 2/8 target. The useful
mechanical result was narrower: `randomNextObject` moved from 95.625% to
98.750% at zero additional generation cost after the wavefront retained a
layout-correct, score-tied candidate and then enumerated the promoted
temporary type. Its last residual is one allocation web, and a bounded
fault-ranked search did not close it. Therefore phase completion may outrank a
scalar tie inside the repair frontier, but only exactness promotes a node.

ABI annotations inferred from a leaf binary remain hypotheses. In particular,
`lbu $v0` proves zero-extension behavior but does not distinguish an original
`u8` return from `u32`. The pilot keeps these alternatives outside the
verified prototype store and shadow-tests them on callers. The sole caller of
`randomNextObject`, `updateRacePickupIdle`, had no compiling stored candidate,
so no parent codegen claim was possible.

The follow-up two-lane pilot separates cheap exact harvest from upward
transfer. Its frozen 16-leaf matchability cohort produced 13 exact m2c results
before generation could help; 14 of the 16 targets were empty returns, so this
is low-hanging-fruit calibration rather than broad solver evidence. A post-hoc
zero-token project-header preflight recovered both non-empty scheduler getters,
and an assembly-certified empty-definition fallback recovered the remaining
no-op, taking the same cohort to 16/16 without a new model call. Future harvest
reports must separate empty, deterministic-m2c, deterministic-context, and
model-attributable exacts.

The same context closure found the headers declaring the parent and every
direct callee, then the headers defining draft-used types and globals. That
moved `updateRacePickupIdle` from generic syntax failures to one private opaque
struct plus undeclared binary globals. A shadow annotation using target-derived
offsets and widths (`pos@0x1c`, `velY@0x60`, `rotation@0x84`,
`variant@0x86`) made the unchanged m2c parent compile at 91.064%, again with
zero model tokens. Semantic field labels remain hypotheses; offsets, access
widths, header provenance, compiler verdict, and object score are recorded
separately.

The planned `randomNextObject` return-transfer claim is refuted on this edge.
The selected project header already declares `u8`; both callsites immediately
apply `& 0xF`, which subsumes byte zero-extension. The `u8` shadow arm compiled
to identical assembly and the `u32` arm correctly failed as an incompatible
header redeclaration. Frontier selection must therefore require a
compiler-active return consumer, not merely a consumed narrow return. Explicit
subsuming masks are now recorded as inactive. The 91.064% parent residual is a
reshape/allocation problem, not an ABI-contract problem.

## Executable differential-debugger wavefront

`eval.differential_wavefront.run` is the callable bridge between the binary
callgraph DAG and the causal debugger/repair controller. It consumes an
audited `eval.dag_pipeline_pilot` census, preserves its leaf-to-caller order,
restores each node's frozen cases and ABI contract, and launches the same
logic-first/exactness controller previously used only for mode 16. Incomplete
coverage is carried as explicit debt but does not prevent repairing a concrete
observed divergence. Every proposal is compiled and replayed; only a
semantic-prefix-preserving improvement becomes the next-round parent.

Launch a wave with:

```text
wsl python3 -m eval.differential_wavefront \
  --census eval/results/dag-pipeline-census-v2.json \
  --output eval/results/differential-wavefront-v2-long.json \
  --rounds 4 \
  --diagnosis-num-predict 8000 \
  --patch-num-predict 2000 \
  --cache-dir /home/grant/decomp/generation-cache
```

The command writes the wave receipt atomically after every completed node and
supports `--resume`. Run the database/lineage audit with the same arguments
plus `--audit-only`.

The first full eight-node wave completed in 2,284 seconds, charged 185,832
local generation tokens, and had no orchestration errors. It produced no new
semantic-complete or byte-exact function. It did, however, retain independent
causal-prefix progress on two functions:

- Mode 40 accepted three repairs. Matching call/checkpoint prefixes advanced
  0 -> 4 -> 8 -> 12 and byte score advanced
  0.0 -> 6.359 -> 13.495 -> 15.291.
- Mode 16 accepted two repairs. Its matching call prefix advanced 24 -> 38,
  write prefix 9 -> 16, and byte score 89.644 -> 89.788.

The other six nodes retained their verified roots. Invalid exact-text patch
emission was the dominant failure, even when diagnoses located the correct
field or call divergence: 16 of 26 rounds were invalid, nine produced a
compiling candidate, and five were accepted. Thus the dispatcher and monotonic
semantic gate transfer, but free-form source replacement remains the primary
repair-worker bottleneck. Prefix progress is not a semantic pass; Mode 40
remains 0/5 and Mode 16 remains 0/7 complete cases in this wave. The receipt audits clean with
18 attempts, 18 lineage edges, and no held-out overlap.

### Continuation waves and structured source locations

The dispatcher now accepts `--parent-wave`, which resolves every prior
`best_attempt_id` from the attempt database and uses that exact retained child
as the next root. `--functions` can select a bounded subset while preserving
the census's DAG order. Patch emission uses numbered source and controller-
resolved line spans instead of trusting copied old text. The normalizer accepts
one to four non-overlapping spans, decimal string line numbers, `start`/`end`
aliases, three-item span arrays, and line-list replacements while retaining
whole-file, growth, forbidden-source, overlap, and semantic-no-op guards.

The full v3 continuation command was:

```text
wsl python3 -u -m eval.differential_wavefront \
  --census eval/results/dag-pipeline-census-v2.json \
  --parent-wave eval/results/differential-wavefront-v2-long.json \
  --output eval/results/differential-wavefront-v3-structured.json \
  --rounds 4 --diagnosis-num-predict 8000 --patch-num-predict 2000 \
  --cache-dir /home/grant/decomp/generation-cache
```

It resumed all eight prior best attempts, completed in 1,913.8 seconds, charged
187,578 local generation tokens, and produced 24 rounds: 15 invalid final
patches, five compiling candidates, zero accepted improvements, zero new
semantic passes, and zero exacts. The receipt audits clean with 17 attempts,
17 lineage edges, and no held-out overlap. Offline replay of all 55 patch and
retry emissions through the tolerant normalizer increased applicable patches
from nine to fifteen without a model call; genuine no-ops remained the largest
class.

A two-round v4 continuation then isolated Modes 16, 53, and 37 after the
normalizer was hardened. Schema-invalid final rounds fell from 9/9 for those
functions in v3 to 1/6. Mode 16 produced two compiling candidates, but neither
advanced its semantic prefix or 89.788 score. Modes 53 and 37 reached source
compilation but their function rewrites were syntactically invalid; compiler
repair did not recover them. V4 charged 50,074 tokens in 1,017.2 seconds,
accepted no improvements, and audits clean with ten attempts, ten lineage
edges, and no held-out overlap. Receipts:
`eval/results/differential-wavefront-v3-structured.json` and
`eval/results/differential-wavefront-v4-normalized-hard3.json`.

The next stopping surfaces are now separated:

- semantic-pass leaves need deterministic compiler-shape search; longer causal
  explanations generated compiling but byte-neutral or regressing edits;
- `updateRacePlayerLeanAngle` has a concrete `player+0x2f6` versus `+0x2f0`
  layout fault, but the model repeatedly restated the same unpadded struct;
- Mode 16 reaches `fixedSine` with different persistent memory even when the
  call argument matches, requiring a memory-delta backward slice;
- Modes 37 and 53 pass the wrong second argument to
  `updateRacePlayerLeanAngle`, while Mode 40 is missing the next
  `calculateFixedAngleFromDeltaXZ` call on most cases; the model sees these
  observables but does not yet construct a minimal compiling edit;
- callgraph order is routing only: repaired callee behavior is not recursively
  executed or linked into parent differential runs, so the current worker is
  not yet a true upward semantic wave.

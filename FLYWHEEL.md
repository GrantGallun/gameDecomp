# Shaped decompilation flywheel

The flywheel is verified memory; [WAVEFRONT.md](WAVEFRONT.md) is the
dependency-ordered, token-budgeted policy that consumes it. Keep that
distinction explicit: retrieval can propose a shape, while wavefront contracts
can mechanically constrain a concrete caller.

The flywheel is a typed retrieval memory, not a training loop.  A function may
enter the library only after the target compiler and byte oracle produce an
explicit exact receipt.  The library then exposes several representations of
that verified work without pretending that resemblance proves a target uses
the same C.

## Bundle shape

`solver.shaped_flywheel` writes a versioned, content-digested JSON graph:

- `nodes.<function>.trust`: exact attempt, strategy and oracle authority;
- `nodes.<function>.machine`: binary-derived size, leaf status, direct calls
  and normalized memory-access shapes;
- `nodes.<function>.source_profile`: mechanical control-flow, call-sequence
  and member-name summary of the compatible exact C;
- `nodes.<function>.exact_source`: the verified full body, retained for strong
  matches and auditability;
- `edges`: binary call edges and explicitly non-authoritative shape-affinity
  hints;
- `outcomes`: paired retrieval receipts, including regressions, with hashes of
  the result artifacts that produced them.

The whole bundle has a stable digest.  Editing membership, a source body,
profile, edge or outcome without rebuilding the bundle is rejected when it is
loaded.

## Retrieval policy

Candidate discovery remains assembly-gated.  Candidates are transparently
reranked using compatible machine facts: instruction-count ratio, leaf status,
resolved-call overlap and memory-shape overlap.  Name-token similarity is
reported as a hint but does not affect the score.

The default context policy is deliberately asymmetric:

- below `0.90` assembly similarity: send trust, rank components, machine facts,
  structural summary and outcome history; withhold the full body;
- at or above `0.90`: also send bounded exact source, labelled as a structural
  proposal rather than target truth.

No retrieval score may promote a function.  The target compiler and byte oracle
remain the only authority.

## Dependency contracts

Every independently solved node may also expose a compact exact-leaf contract:

- compiler-compatible prototype and return ABI;
- binary-bound parent arguments;
- load width, signedness, byte offset, and compatible element index;
- caller-visible effects; and
- concrete return consumers in the parent.

`solver.callsite_contracts` constructs and renders these contracts without
copying the completed body. It also validates compiled parent assembly against
argument facts and normalized return-consumer flow. Equivalent disassembler
spellings (`0x4`/`4`, `or ..., $zero`/`move`) are canonicalized so syntax does
not create fake activation. This validator applies to every proposal arm,
including the control: a higher-scoring candidate that contradicts an exact
child is not a viable anchor for repair.

The default policy is **validate and repair on mismatch**, not unconditional
contract injection. Direct body injection and generic semantic annotations
remain opt-in research arms because their paired DEV results were mixed and
produced no exact gain.

## Build a graph

```text
python3 -m solver.shaped_flywheel \
  --db ~/decomp/kb-sbk1.sqlite \
  --out eval/results/flywheel-shaped.json \
  --compiler "IDO 5.3 -O2" \
  --outcome-pair control-r1.jsonl treatment-r1.jsonl
```

Each `--outcome-pair` adds valid paired rows and excludes zero-draw
infrastructure failures.

`python3 -m eval.flywheel snapshot` now writes both the legacy exact-source
pool and a shaped graph.  Its experiment manifest contains three explicit arms:

1. context-free control;
2. legacy whole-source sibling context;
3. shaped context.

Run the shaped arm directly with:

```text
python3 -m eval.run_set ... --pipeline --siblings \
  --shaped-sibling-pool eval/results/flywheel-shaped.json
```

Legacy and shaped pools are mutually exclusive and have different experiment
fingerprints, so their result files cannot be resumed into one another.

## Current receipt

`eval/results/flywheel_post_138-shaped-pool.json` contains 138 exact nodes,
202 typed edges and 14 paired outcome receipts (digest
`5275ec84ba0dc4cd`).  It is a DEV artifact; the
SBK2 held-out test must use a separately frozen, preregistered policy rather
than importing DEV outcome tuning after seeing held-out results.

`eval/results/wavefront_token_budget_plan.json` is the frozen pre-activation
frontier manifest. The subsequent zero-token scan
`eval/results/wf1_wavefront_activation_final.json` found that all four
replayable eligible candidates already satisfy their exact-leaf argument and
return-flow contracts; the fifth candidate no longer replays. Therefore zero
of 2,400 reserved generation tokens were spent. Future manifests must treat
candidate mismatch prevalence as an eligibility condition, not merely rank
parents by the amount of leaf information available.

The 2026-09-01 ABI enrichment pilot adds a second eligibility lesson. Narrow
return/parameter evidence and caller fanout successfully identify
compiler-active leaves, but selection is not promotion: eight draws yielded
zero exact leaves. Phase-aware deterministic replay did improve
`randomNextObject` from 95.625% to 98.750% with no new generation, while the
remaining one-web allocation residual resisted a bounded search. Its inferred
`u8` versus `u32` return alternatives are stored only in the experiment
receipt, not this verified flywheel, and its sole caller had no compiling
baseline on which to test codegen transfer.

The two-lane follow-up clarifies what parent memory must contain before compact
callee contracts can matter. Project-header closure recovered two real getters
as exact and supplied six direct-callee headers plus three type/global headers
to `updateRacePickupIdle`. A binary-derived shadow struct then made that parent
compile at 91.064% with zero generation tokens. These annotations are useful
flywheel inputs but not exact-node promotions: store binary offsets and widths
as facts, header declarations as build context, semantic field labels as
retractable hypotheses, and the compiler/oracle receipt as the outcome.

Return-contract edges also need an activation label. Both
`randomNextObject` consumers mask with `0xF`; the project header already says
`u8`, the u8 arm was assembly-identical, and u32 contradicted the build header.
Such edges stay in the graph for provenance but are excluded from ABI-transfer
budget ranking. A consumed return is not automatically compiler-active.

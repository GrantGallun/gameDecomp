# Frozen-sibling A/B: snapshot `5d80b4d9f1884dc9`

## Question

Does injecting verified exact source from an assembly-similar function improve
the solver, or does it merely bias it toward code already seen?

This is retrieval-conditioned generation, not reinforcement learning.  The
test therefore measures transfer where the retriever has coverage; it says
nothing about autonomous learning on the 27 active targets for which the
production retriever returns no context.

## Frozen protocol

- Exact-source pool: 136 functions (94 medium-or-larger).
- Active panel: 34 DEV functions.
- Treatment-eligible cohort: 7 functions with similarity at least 0.45.
- Strong twins: 2 at similarity above 0.90.
- Exploratory analogues: 5 at similarity 0.45-0.75.
- No-context targets excluded: 27.  Their control and treatment prompts would
  be identical under the production retriever.
- Model: `gpt-oss:20b`; best-of-4 pipeline; two paired repeats.
- Treatment used the immutable verified-source bundle
  `flywheel_post_136-sibling-pool.json`.

The initial control repeat had two zero-draw SQLite lock failures.  They were
excluded and rerun at the identical sampling budget after adding the same
120-second busy timeout already used by the heavier evaluation harnesses.

## Results

| Function | Similarity band | R1 control | R1 context | R1 delta | R2 control | R2 context | R2 delta |
|---|---:|---:|---:|---:|---:|---:|---:|
| `updateRacePlayerMode16AerialTrick` | 0.90+ | 0.000 | 0.000 | +0.000 | 0.000 | 59.788 | +59.788 |
| `updateCourseSelectCourseDescription` | 0.45-0.75 | 86.797 | 99.560 | +12.763 | 0.000 | 68.684 | +68.684 |
| `initControllerPakRaceRecordSaveFlow` | 0.45-0.75 | 40.625 | 67.368 | +26.743 | 0.000 | 0.000 | +0.000 |
| `resetAllViewports` | 0.45-0.75 | 48.562 | 54.658 | +6.096 | 26.233 | 23.904 | -2.329 |
| `packFixedTransformMatrix` | 0.45-0.75 | 34.160 | 30.394 | -3.766 | 47.237 | 87.404 | +40.167 |
| `updateRacePlayerMode37AerialTrick` | 0.45-0.75 | 0.000 | 0.000 | +0.000 | 0.000 | 0.000 | +0.000 |
| `updateRacePlayerMode53AerialTrick` | 0.90+ | 0.000 | 0.000 | +0.000 | 0.000 | 0.000 | +0.000 |

| Repeat | Control mean | Context mean | Delta | Control exact | Context exact |
|---|---:|---:|---:|---:|---:|
| 1 | 30.021 | 35.997 | +5.977 | 0/7 | 0/7 |
| 2 | 10.496 | 34.254 | +23.759 | 0/7 | 0/7 |
| Pooled | 20.258 | 35.126 | +14.868 | 0/14 | 0/14 |

Database audit: `pragma quick_check` returned `ok`.  Every treatment target had
logged routed attempts containing both the sibling-context marker and its
expected sibling name (8 logged prompts per target in the two-hour audit
window).

## Verdict

The preregistered decisive criterion was **not met**: sibling context produced
no additional oracle-exact functions.  The supporting criterion was met:
mean best score improved in both repeats without zero-draw failures after the
runner fix.

The effect is real but unstable.  `updateCourseSelectCourseDescription` is the
only function with a positive paired delta in both repeats.  Other functions
changed sign or benefited in only one repeat.  Raw similarity does not predict
transfer: one 0.943 twin improved only once, the 0.927 twin never improved, and
the 0.469 analogue produced both a regression and a 40-point gain.

The defensible claim is therefore: verified sibling context diversifies the
proposal distribution and can expose much better compiling basins, including
across weak analogues, but it is not an autonomous solver and should not
replace the context-free arm.

## Flywheel v2 policy

1. Run context-free and retrieved proposals as a portfolio; let the byte oracle
   retain the better candidate.  Never make retrieval the only arm.
2. Counterbalance arm order per repeat (A/B then B/A), or interleave by
   function, and record a sampler seed when the backend supports it.  Both
   repeats here ran control first, so model/server drift remains a possible
   contributor to the aggregate score gain.
3. Calibrate retrieval by function family and observed paired outcomes, not a
   single global assembly-similarity threshold.
4. Prefer extracted invariants (CFG skeleton, call order, field offsets,
   signedness and loop shape) alongside or instead of full sibling bodies to
   reduce anchoring.
5. After repeated 0%-compiling basins, stop sampling.  Escalate to a scaffold
   lane: Ghidra CFG/data-flow, cross-game provenance, compiler-transform
   inversion, or human-shaped pseudocode before resampling.
6. Treat the 27 no-context targets as a separate coverage problem.  Retrieval
   performance must never be reported as general performance on those targets.

The immediate high-value residual is
`updateCourseSelectCourseDescription` at 99.560%; it should go to deterministic
diagnosis/repair rather than another broad sampling pass.

# Logic-first reconstruction lane

## Architectural decision

The repository already had the right mechanical substrate: binary call edges,
CFG recovery, fixed-point register/dataflow facts, exact-callee contracts,
compiler receipts, and the byte oracle. It did not have the right orchestration
for logic-first work. Every useful non-exact candidate collapsed into one repair
frontier, and weighted or byte residuals dominated routing.

The logic-first lane adds two pieces without weakening the existing ratchet:

- `solver/logic.py` compares register-independent calls, control-flow shape,
  non-stack memory effects, and opcode sequence. It never labels a non-exact
  candidate semantically equivalent.
- `eval/logic_first.py` freezes a call-connected DEV cluster from binary edges
  and existing candidate provenance, recompiles every root, records lineage,
  and emits stage receipts.

Non-matching C still cannot replace assembly in the real build. Byte exactness
remains the only commit condition.

## Stage model

| Stage | Meaning | What it does not mean |
|---|---|---|
| `not_compiling` | No comparable object exists | Nothing about the intended logic |
| `compiling_candidate` | C compiles, but broad machine shape fails a gate | Not semantically wrong in every respect |
| `logic_shape_candidate` | Calls, control, effects, and opcode metrics clear broad thresholds | Not behavioral equivalence |
| `structural_candidate` | Machine shape is tightly aligned while ignoring register/stack noise | Not byte exact |
| `byte_exact` | The authoritative object oracle reports exact | Terminal success |

For every non-exact stage, `semantic_status` remains `not_tested`. A future
differential-execution receipt must be separate from these assembly metrics.

That separate receipt now has a validated narrow pilot. The dependency-free
runner in `solver/mips_differential.py` executes target and compiled-candidate
MIPS from identical seeded states and compares call arguments, call-time and
final persistent memory, returns, and ABI preservation. On mode 16, an
independently formatted raw-target control passed 3/3 cases while the audited
v5 false-positive candidate failed 3/3 at the expected clamp-vector argument
(`player+0x40` versus `player+0x1c`). This validates the pilot mechanism on one
function, not general behavioral equivalence; see `DIFFERENTIAL_DEBUGGER.md`.

## First connected baseline

Manifest: `eval/sets/logic_first_connected_dev_v1.json`  
Receipt: `eval/results/logic-first-connected-dev-v1-baseline.json`

The metadata-only selector chose eight existing non-held-out candidates linked
by eleven binary call edges. Six functions belong to the aerial-trick race
player translation unit; `fixedSine` and `randomNextMain` are direct binary
dependencies.

| Function | Stage | Calls | Memory | Control | Opcodes | Byte distance |
|---|---|---:|---:|---:|---:|---:|
| `resetRacePlayerTrickSubstate` | structural | 1.000 | 1.000 | 1.000 | 1.000 | 7 |
| `updateRacePlayerMode53AerialTrick` | compiling | 1.000 | 0.263 | 0.998 | 0.981 | 81 |
| `updateRacePlayerMode16AerialTrick` | compiling | 1.000 | 0.704 | 0.912 | 0.961 | 209 |
| `updateRacePlayerMode37AerialTrick` | compiling | 1.000 | 0.100 | 0.892 | 0.808 | 430 |
| `updateRacePlayerLeanAngle` | compiling | 1.000 | 0.000 | 0.731 | 0.718 | 163 |
| `updateRacePlayerMode40Stun` | compiling | 0.000 | 0.000 | 0.255 | 0.019 | 413 |
| `fixedSine` | logic shape | 1.000 | 1.000 | 0.812 | 0.909 | 76 |
| `randomNextMain` | logic shape | 1.000 | 1.000 | 0.821 | 0.974 | 67 |

The result changes routing. `resetRacePlayerTrickSubstate` belongs in exactness
polish. `fixedSine` and `randomNextMain` need behavioral testing or control-shape
reconstruction before polish. The three aerial-trick handlers preserve their
direct calls and much of their control/opcode shape, but their memory-effect
profiles expose missing or incorrect state behavior that the weighted score
alone hid. `updateRacePlayerMode40Stun` needs full logic reconstruction.

Aggregate: eight of eight roots compiled; zero were exact; one structural and
two logic-shape candidates were identified. Calls averaged 0.875, memory effects
0.508, control structure 0.802, and opcode sequence 0.796. No function has a
behavioral-equivalence receipt. The lineage/held-out audit passed with eight
parents, eight edges, and zero held-out overlap.

## Next experiment

Build one shared module packet containing the frozen binary call graph, target
profiles, stable memory identities, settled exact-callee contracts, and each
active C root. Give the same fixed packet and generation budget to:

1. a function-local exactness prompt; and
2. a logic-first prompt that must explain and repair missing calls, branches,
   and memory effects before considering register allocation.

Rank non-exact children by stage, then calls, memory effects, control structure,
and opcode sequence. Keep byte distance as a shadow metric. The first treatment
targets should span the observed regimes: `updateRacePlayerMode16AerialTrick`
near the logic gate, `updateRacePlayerMode53AerialTrick` with a focused effects
gap, and `updateRacePlayerMode40Stun` requiring a full reshape. Only after the
logic lane improves should equal-budget exact polishing be compared.

The non-exact rank is implemented as `solver.logic.quality_key`; weighted score
and byte distance are intentionally absent. Frozen shared-context packets for
the three targets are available as `eval/results/logic-first-packet-mode16-v1.json`,
`logic-first-packet-mode53-v1.json`, and `logic-first-packet-mode40-v1.json`.
Each contains all eight binary target profiles and eleven call edges, but no
target reference C. These packets are the fixed inputs for the model comparison.

## Updated seed priority

For logic-first work, a mechanically generated m2c draft outranks a later
model rewrite when it retains more target-derived control/data flow. The
pipeline must first try logic-preserving adaptations: explicit linker constants,
project prototypes/layouts, and compile-only declaration repair. It then gates
the result with target-selected semantic stress cases. Object similarity is a
shadow metric until those cases pass.

This ordering matters in practice. On `updateRacePlayerLeanAngle`, the existing
model C scored 51.797% but failed the frozen semantic panel. A mechanically
adapted m2c draft scored only 31.169% yet passed all 512 differential cases.
Another operation-shaped spelling of the same behavior passed all 512 and
scored 80.508%. These are logic and exactness candidates respectively; neither
may enter the real build until the byte oracle reports exact.

The priority is now enforced by the DAG wavefront. Before any model call it
runs target-side coverage exploration, retains m2c's logic, adds only
evidence-backed compile context, and evaluates a dimension-balanced semantic
panel. Uncovered equality/zero/sign branch outcomes become concrete input
gradients by tracing the predicate operand back to its last memory load.
Predicate-directed cases are scheduled breadth-first ahead of broad mutation
sweeps.

On the four previously difficult race-player handlers this route passed all
1,024 differential executions with complete instruction and branch-edge
coverage on both target and compiled candidate. It used zero LLM tokens. Their
weighted object scores remain 91.563, 97.212, 97.557, and 96.149, and none is
byte exact. The correct routing is therefore exactness polish next, not another
whole-function semantic rewrite. See
`eval/results/differential-wavefront-v9-m2c-predicate-gradient.json`.

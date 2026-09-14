# Finished-decomp reference teacher

The 100%-matched SBK1 source is a governed teacher corpus, not an unlabelled
solver input. `solver/reference_teacher.py` builds tamper-evident packets under
two explicit regimes:

| Regime | Available reference source | Valid claim |
|---|---|---|
| `leave_one_function_out` | Other finished functions, including the target TU | Same-game target-excluded teacher assistance |
| `leave_one_tu_out` | Finished functions outside the target TU | Same-game TU-excluded teacher assistance |

Neither regime is cold-start or cross-game autonomous decompilation.

## Exclusion and provenance

The builder maps binary TU object names back to finished source paths, then
reads the target definition only to create raw and normalized exclusion hashes.
It rejects:

- the selected target by name;
- any function with the same normalized source under another name;
- every function from the target TU in the LOTO arm;
- oversized or missing definitions;
- matched-source blocks whose parent function is not an admitted example.

Every emitted example records function, TU, relation, assembly similarity,
instruction count, raw and normalized source hashes, mechanical source profile,
and exact reference source. Relations distinguish same-TU, binary-call neighbor,
logic-cluster member, and assembly-similar retrieval.

Nested matched-source blocks are also emitted. They are labelled unaligned
ranking hints: their parent functions are byte-exact, but the block is not
claimed to correspond to a particular target basic block.

Packets have a content digest. `reference_teacher.load_packet` refuses edited
membership, regime changes, target/normalized duplicates, target-TU leakage,
unowned blocks, or a failed contamination audit.

## First packet build

The first build uses the frozen eight-function logic-first race-player cluster
and three targets:

- `updateRacePlayerMode16AerialTrick`
- `updateRacePlayerMode53AerialTrick`
- `updateRacePlayerMode40Stun`

It produced six packets containing 29 exact sibling functions and 36 smaller
matched-source blocks. All six contamination audits and all six tamper-evident
loads passed.

LOFO correctly surfaces the other same-TU aerial-trick handlers. For example,
mode 16 receives the exact mode 53 and mode 37 handlers plus the shared reset,
lean, and sine helpers. LOTO removes every same-TU handler: modes 16 and 53 then
receive only `updateRacePlayerLeanAngle`, `fixedSine`, and `randomNextMain` from
outside the target TU. Mode 40 additionally retrieves external assembly-ranked
rendering functions.

Receipt:
`eval/results/reference_teacher/logic-first-reference-teacher-v1-receipt.json`

## Evaluation design

The next logic-repair comparison has three fixed context arms:

1. binary-only logic packet;
2. binary plus LOFO teacher packet;
3. binary plus LOTO teacher packet.

Use identical candidate roots, model calls, seeds, compile budgets, and
logic-first rank. Report stage movement, calls, non-stack memory effects,
control structure, opcode sequence, compilation, and exactness. A LOFO-only win
is useful for finishing SBK1 but indicates dependence on same-TU future
knowledge. A LOTO win is stronger evidence that the corpus teaches transferable
compiler/source idioms. Neither is cross-game evidence.

The executable comparison is `eval/logic_reference_ab.py`. It rotates arm
order across targets, reuses identical roots and seed schedules, writes every
model proposal and compiler attempt to the lineage database, and selects
non-exact children only with `solver.logic.quality_key`. Generated sources and
an incremental receipt are retained so interrupted local-model runs can resume
without silently changing the experiment.

Within an arm, later rounds follow the prior child and receive its exact
compiler error or instruction diff. The evaluator tracks the global best
separately, so an investigative regression cannot displace the frozen root.

## First equal-budget model result

Receipt: `eval/results/logic-reference-ab-v5.json`

The frozen three-target pilot used GPT-OSS 20B, two sequential calls per arm,
identical per-function seeds, a 1,800-token response cap, rotating arm order,
and `logic.quality_key` selection. All 17 compiler attempts had lineage edges;
the 18 proposals and receipt IDs reconciled with the database, and held-out
overlap was empty.

| Arm | Calls | Compiling children | Logic-rank improvements | Byte-distance improvements | Exact |
|---|---:|---:|---:|---:|---:|
| binary only | 6 | 2 | 0/3 | 0/3 | 0 |
| LOFO | 6 | 1 | 1/3 | 1/3 | 0 |
| LOTO | 6 | 3 | 1/3 | 0/3 | 0 |

LOFO improved `updateRacePlayerMode53AerialTrick`: control structure reached
1.0 and positional byte distance fell from 81 to 65, while memory-effect
agreement remained 0.263. LOTO improved the logic rank for mode 16 by raising
memory-effect agreement from 0.704 to 0.731 and opcode agreement from 0.961 to
0.966; control dipped from 0.912 to 0.909 and byte distance worsened from 209
to 221. Neither candidate crossed the logic-shape stage gate. No arm produced a
compiling child for the near-empty mode 40 root.

Both retained improvements came from the first call. The compiler/diff
feedback call produced no new best in any of the nine trajectories. Raw
same-TU source also caused unavailable-field/lvalue failures often enough that
LOFO compiled only one of six children. The result is therefore positive but
inconclusive evidence for governed reference context, and negative evidence
for the present one-step feedback adapter. The next test should combine the
teacher with deterministic project-header/type closure and an identifier/field
adapter, rather than adding more raw source or more unconstrained repair calls.

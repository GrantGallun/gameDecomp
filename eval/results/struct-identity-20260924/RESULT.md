# Result: binary-only struct identity: precise and layout-correct, recall partial

Protocol: `PROTOCOL.md` (amendments A1 and A2 dated, each before the CHECK run it governs). Binary only:
`identity.py` walks the ELF's 2,113 functions (19,259 typed accesses, 8,791 call-edge arguments, 847 pointer
stores, 945 returns). `score.py` solves with union-find plus structural merge and scores against
`eval.ground_truth` (1,117 labeled parameter slots, 118 reference structs), split FIT/CHECK by name hash.

## CHECK results
| variant | B-cubed precision | recall (baseline 0.170) | layout agreement | note |
|---|---|---|---|---|
| A, no call edges | 1.000 | 0.170 | n/a | intra-procedural only |
| B, every call edge as equality | 0.523 | 0.448 | 0.16 | generic helpers merge everything |
| C24, hub cap (pre-registered) | 0.955 | 0.199 | 0.80 | fires test failed |
| D16 after A1 | 0.977 | 0.245 | 0.959 | passes the fires test |
| **D16 after A2 (final)** | **0.952** | **0.311** | **0.976** (1,609 agree / 39 disagree) | fires: RaceIntroEffectActor 0.65 |

**Verdict: partial** (precision >= 0.90, recall < 0.50).

## What worked (each found on FIT, pre-registered before CHECK)
- **Only parameters the callee dereferences carry type evidence.** Generic helpers (`setCallbackTaskCallback`,
  `addRenderCallback`, heap helpers) never touch their argument's fields. Unifying through them merges every actor
  type (variant B's 0.52 precision).
- **Callback co-passing**: a call passing a function address g together with one object x: P(g,0) = x
  (110/111 on FIT). This is the actor state-machine glue.
- **Binary arity**: a call edge only for arguments the callee reads before writing (A2). Stale a2/a3 registers
  had fabricated edges and hidden co-passing sites.

## What did not
- Self-registration (a function-address-only call links the callback to the caller's own object): 108 agree, 60
  disagree on FIT. Spawners (`createCallbackTask(..., initX)`) create a new object of another type. Not used.
- Remaining recall is lost to indirect calls through function-pointer tables (`RacePlayer` mode handlers) and
  object arrays walked in loops, which a linear walk with conservative branch merges does not follow.

## Reach and a correction to the premise
Unsolved population: 25 of 206 functions gain cross-function offsets (median 10; `population_reach.json`). More
importantly, compiling and matching ONE function needs only the fields that function touches. Offsets learned from
other functions do not change its object. Cross-function identity matters for consistency (prototypes, pointer-field
types, struct size) but is not the main lever for single-function matching. The next question is which PART of the
reference's type information makes m2c's drafts match (`../context-ablation-20260924/`).

## v3: CFG dataflow (amendment A3): the reading changes to "gets us somewhere"
Replacing the linear walk's conservative branch-merge reset with forward dataflow over basic blocks (meet = equal in
every predecessor, to a fixed point) raises typed accesses from 19,259 to **32,562** (+69%), call-edge arguments 8,791
to 12,205, pointer stores 847 to 1,788. FIT selects D32 by the same rule. CHECK:
| | precision | recall (baseline 0.170) | layout agreement |
|---|---|---|---|
| v2 D16 | 0.952 | 0.311 | 0.976 |
| **v3 D32** | **0.979** | **0.547** | **0.989** (5,473 agree / 63 disagree) |
Fires test: six structs with >= 10 slots at recall >= 0.5 (all four ending-credits actors, RaceIntroEffectActor,
RaceUiCourseStatsActor). **Verdict: gets us somewhere.** The downstream test is `../context-ablation-20260924/`
(BINARY arm) and `../binary-types-capability-20260924/`.

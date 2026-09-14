# Can uopt's register allocation be predicted from target assembly?

2026-09-12. Tool: `eval/uopt_replay.py` (tests: `tests/test_uopt_replay.py`, 14).
Graveyard: HYP-20260912-01. **Answer: no -- not from post-allocation assembly.**

## Method

Every target dump is IDO's own output, so every function -- matched or not --
is ground truth. For each function: rebuild webs and interference, replay
greedy lowest-free colouring in four priority orders, and count registers that
land exactly where IDO put them. Separately, with every neighbour in its ACTUAL
register, check whether IDO's choice is free (soundness) and whether it is the
lowest free one (the selection rule). ABI webs (parameters, call arguments and
results, return values), dead definitions, frame saves and jump-table functions
are excluded from scoring.

## Results (1,279 functions, 23,332 scored webs)

| reconstruction | model | oracle | chrono | random | IDO choice free | IDO took lowest free |
|---|---|---|---|---|---|---|
| linear splitting (first version) | 13.1 | 16.3 | 12.5 | 12.6 | -- | -- |
| **reaching defs over CFG** (`report-instruction.json`) | **17.1** | **24.6** | 15.4 | 15.3 | **100.0** | **25.4** |
| block-granular live ranges (`report-block.json`) | 11.8 | 15.0 | 10.1 | 9.1 | 40.5 | 14.1 |

- Linear webs were fragments: 51.7% single-occurrence. Control-flow webs: 0%.
- CFG webs are sound: IDO's choice is always free, and 100% of values live across
  a call are in s0-s8. Every oracle miss picked a LOWER register than IDO.
- Block granularity forbids IDO's actual choice 60% of the time: too dense.
- With neighbours coloured correctly, 9,196 of ~10k held-out non-crossing webs see
  **12+ free registers**. The reconstruction cannot see what constrained IDO.

### No selection rule fits (held-out functions)

| rule | agreement |
|---|---|
| spec pool order, v0 first | 26.2% |
| preference order derived from training half | 26.7% |
| round-robin after previous web | 27.7% |
| uniform random among free | 8.6% |

## Four silent-failure traps found on the way

1. Linear def-use splitting fragments webs (above).
2. `cfg.build` resolves hex branch targets only behind the
   `# MIPS_DIFF_NUMERIC_BRANCH_BASE` marker; without it every branch is an
   unknown successor. Deliberate, but any caller that omits it gets straight
   lines silently.
3. "First register found" misreads writes: `lwc1 f0,0x4(a0)` looks like a write
   of a0, `mult a0,a1` like a write of a0, and branch offsets `a0`/`a4` like
   argument registers.
4. `uopt.REG` does not match `ra`, so a `jr ra` test through it never fires.

## What this retires, and what it opens

The "79% concordant" claim (commit e763f40) was already superseded by 50-58% in
`solver/liveness.py`; this removes the remaining premise that a better
reconstruction or a different save formula would rescue it.

uopt's own view is obtainable. `cc -K` keeps every intermediate file, including
**`.O`, uopt's optimised u-code**, written after register allocation and before
`ugen`; `uopt -v` also prints a per-function figure (`f(5)`). Decoding `.O` for a
candidate compile would show uopt's actual variables and register decisions --
the causal signal no reconstruction from assembly provides. It is binary
u-code, so the first step is a probe (compile controlled variants and diff the
bytes) rather than an assumed format.

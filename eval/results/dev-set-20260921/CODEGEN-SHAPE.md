# What the remaining distance actually is

Read off the oracle's own normalized object dumps for the four highest-scoring members of the 17-state
development set, classified with `solver.signals.analyse` (the project's supported classifier, not a new
one). Receipt: `codegen-shape.json`; producer: `_codegen_shape.py`; frame: `dev-set.json`.

| function | score | instruction delta | classification of the differing lines |
|---|---|---|---|
| `__MusIntProcessWobble` | 97.045 | **0** | regalloc 9, structural 2 |
| `updateRacePlayerAirborneLaunch` | 94.822 | −3 | regalloc 77, structural 27 |
| `osEPiRawWriteIo` | 91.474 | **+1** | regalloc 6, structural 3, reloc 2 |
| `updateRacePlayerMode06TerrainFall` | 90.451 | **+1** | regalloc 81, structural 19, ordering 4, immediate 2 |

**How to read the numbers.** `signals.analyse` classifies the *differing lines* of the diff, so "regalloc 81"
means 81 of the differing lines look like register-allocation differences — a measure of the shape of the
residual, not a count of independent defects. Three of the four are within one instruction of the target;
their score is not low because instructions are missing.

## The shape: register allocation, and one stack frame

**`__MusIntProcessWobble` — a permutation, nothing else.** 22 instructions in both, the same opcodes in the
same order, and a shifted register chain:

```
target      lbu t6,0x107(a0)   addiu t7,t6,-0x1   andi t8,t7,0xff   bnez t8,54   sb t7,0x107(a0)
candidate   lbu v0,0x107(a0)   addiu t6,v0,-0x1   andi t7,t6,0xff   bnez t7,54   sb t6,0x107(a0)
```

Every operand is the same memory location; only the register names move. `v0` is the return-value register,
so the candidate is keeping a value live in `v0` across the block where the target used the caller-saved
`t6` chain.

**`osEPiRawWriteIo` — one extra materialisation, one reused register.** The target loads the status word
into `a3` and reuses it (`lw a3,%lo(PI_STATUS_REG)(t6)`, then `lw a3,%lo(PI_STATUS_REG)(t8)`); the candidate
uses `t6` and then `t8`, and carries an extra

```
candidate   addiu t2,t2,%lo(D_A0000000)
```

where the target materialised `at` (`lui at,%hi(D_A0000000)`) instead. 19 lines against 18.

**`updateRacePlayerAirborneLaunch` — registers shifted, branch displacements off by 4.** `lh t6,0(v0)` in
the target against `lh t5,0(v0)` in the candidate, and the whole `subu/addiu/andi` chain moves from
`v1,t0` to `t0,t1,t2`; branch targets read `1b8` against `1c4`. Three instructions fewer in the candidate,
none of them a different operation.

**`updateRacePlayerMode06TerrainFall` — the one genuinely different shape: the stack frame.**
`addiu sp,sp,-0x40` in the target against `addiu sp,sp,-0x60` in the candidate, and `li t7,0x3c` placed
before `or t1,t9,at` in the target against `addiu t7,v1,1` / `li t8,0x3c` / `or t9,t6,at` in the candidate.
A frame 32 bytes larger and one extra instruction is a source-level declaration difference (what is live
across the call), not a missing algorithm.

## What this means for the catalog

The nine-step intake sequence owns **declarations** — placeholders, headers, globals, struct layouts,
negative offsets, OR-addresses. None of that is the residual here: on these four states the declarations are
already right, the code compiles, clang accepts it, and the instructions are the same ones in a different
register assignment.

The registry already advertises the operator for that shape and the search over the intake subset simply
could not see it:

```
eval.tool_registry.ACTIONS["regalloc-search"]   kind=transform
    needs = candidate, compile_fn, target_dump
```

All three inputs are populated on a real context. That is the concrete next experiment, and it is a
measurement rather than a guess: search the **registry** catalog (10 transforms instead of 9, including
`regalloc-search`, `diffrepair`, `invert-mutations`, `redraft`) from the fixed order's own candidate and
from the frozen draft, on the same 17 states, with the same compile allowance. `bounded_search.py
--catalog registry` is that run, and `destructive_moves` must stay 0 while it happens.

# Protocol: what the `register` storage class does in IDO 5.3 at -O1 (non-leaf) and at -O2

Written 2026-09-24 before `probe.py` and `ablate.py` ran. Motivation: `draft-reference-mining-20260924`
(deduplicated) links `R:opcode:move/sw` (target `move`, candidate `sw`) and `R:opcode:addiu/lw` to the reference
adding `register` (lift 33 and 12.6). Exploration (`mining_register.py`, `population_register.py`, not evidence):
the 19 mining references that add `register` are 12 libultra functions at -O1, all non-leaf and typically
`saveMask = __osDisableInt()`, plus 7 game functions at -O2. Of the 26 population functions with that signature,
8 are -O1 non-leaf libultra and 18 are -O2. H2 (2026-09-24) confirmed `register` only for -O1 LEAF functions.

## H6: -O1 non-leaf (paired synthetic compiles, -O1 libultra recipe; `probe.py`)
At -O1, a plain local assigned before a call and used after it is kept in its stack slot (`sw` after the
assignment, `lw` before the use). Declared `register`, it is held in a callee-saved register (`move s?,v0` or
direct), with no stack slot for it.
- P6a: `u32 f(void) { u32 m; m = g(); h(); return m; }`. The plain local has an `sw` of v0 and a later `lw`;
  the `register` local has neither, and has a `move s0,v0`.
- P6b: the same with a parameter: `void f(u32 a) { h(); k(a); }` versus `void f(register u32 a)`. The plain
  parameter is stored to its incoming home (`sw a0,N(sp)`) and reloaded; the `register` one is not stored.
- P6c: a local used only before any call (`u32 m = x * 3; k(m);`). The plain local has an `sw`; the `register` one
  does not.
- P6d: the frame of the `register` variant is no larger than the plain one.
Confirmed only if all four hold.

## H7: -O2 ablation on the mining references (`ablate.py`, mining functions only)
For each of the 19 mining references that use `register`, compile it unchanged (must be exact modulo relocation
names) and with every `register` keyword removed.
- P7a: every -O1 reference changes when `register` is removed.
- P7b: at -O2, `register` is inert: every -O2 reference compiles to the identical object without it.
If P7b fails, the functions that change are listed, and the change (which opcodes) is the start of an -O2 rule.
Neither outcome is assumed.

## Use
- H6 confirmed: extend `solver/branch_shape.o1_register_local` from leaf-only to non-leaf, proposing `register`
  on locals and on parameters. The compiler decides; the gate is an -O1 recipe plus the `move/sw` or `nop/sw`
  signature. It needs a fire test on the motivating residual (`__osSetGlobalIntMask`, `__osResetGlobalIntMask`),
  then the 8 -O1 population functions, then the paired population rerun with 0 exacts lost.
- P7b holds: the 18 -O2 population functions are out of reach for this lever; record it and do not build an -O2
  variant.

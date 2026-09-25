# Result: the `register` lever is -O1 only, and it closed two functions

Protocol: `PROTOCOL.md`. Lead: `draft-reference-mining-20260924` (deduplicated `opcode:move/sw` -> `register+`).

## H7, ablation on mining references (`ablate.py` -> `ablate.json`): confirmed
Removing every `register` keyword and recompiling: **-O1: 14/14 objects change** (the target has 2-3 more `move`,
fewer stack stores). **-O2: 36/36 byte-identical.** `register` is inert at -O2, so the mining link at -O2 was
correlation, and the 18 -O2 population functions with the signature are out of reach for this lever.

## H6, synthetic -O1 compiles (`probe.py` -> `results.json`): refuted as registered
- P6a holds: a plain local assigned from a call and used after another call is stored and reloaded
  (`sw v0,0x1c(sp)` / `lw v0,0x1c(sp)`); as `register` it is `move s0,v0` with s0 saved.
- P6b fails: `register` on a parameter changes nothing (the `sw a0` home store stays).
- P6c fails: a `register` local NOT live across a call still takes s0 (frame 32 -> 40).
- P6d fails: the `register` frame is larger (the s-register save).
Rule as observed: at -O1 a `register` local always takes a callee-saved register and grows the frame by its save;
parameters are unaffected. Catalogued as `ido53-o1-register-local-saved`.

## Mechanism: `solver.branch_shape.o1_register_saved`
Gate: an -O1 recipe and a callee-saved save in the target (`-sw sN`) that the candidate lacks. It marks locals
`register`, the ones m2c named after the wanted register first, each with and without a pipeline `framePad`. Tests:
fires on `__osSetGlobalIntMask`; declines at -O2, without the save, and when the candidate already saves it.

## Fire test (`fire.py` -> `fire.json`; 7 -O1 population functions carrying the signature)
| function | before | after |
|---|---|---|
| __osSetGlobalIntMask | 75.833 | **100.0** (`temp_s0` + no pad) |
| __osResetGlobalIntMask | 79.286 | **100.0** (`temp_s0` + no pad) |
| osStartThread | 85.226 | 89.058 |
| __osPfsRWInode, __osPfsSelectBank, osPfsIsPlug | | decline: the target saves no s-register; the signature came from sp offsets under a too-large frame |
| osPiRawStartDma | | decline: no locals; the target holds the PI status read in s0/s1, which needs a NEW register local (the libultra `WAIT_ON_IOBUSY(stat)` idiom), not a marking |

## Recording (`record.py` -> `inventory-receipts.json`)
The first confirmation attempt (95895) was object-exact but the frontend gate refused the C: `__osDisableInt` and
`__osRestoreInt` were implicitly declared. The existing `frontend_type_repair` added the prototypes, and the fresh
recompiles were exact with the gate passed: `__osResetGlobalIntMask` receipt 95897, `__osSetGlobalIntMask` receipt
95899, both source-independent. `eval.status` after: 388 byte-exact, **SOLVED 277** (275 before these two).

## Not done
The paired population rerun that installs a family (0 exacts lost) has not been run. The family is wired into
`branch_shape.families()`, and its gate is narrow (-O1 plus a target-only callee-saved save).

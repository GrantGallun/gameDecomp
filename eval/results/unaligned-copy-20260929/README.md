# Unaligned struct copy: a residual class found by reading width stalls

2026-09-29. Found while reading the width-stage stalls of the class-by-class search
(`../composed-edits-20260929/width_stalls.py`, `width_case.py`). The "extra `sll 0x10/0x18`" in those
functions wasn't sign extension. It was byte packing, `(b0 << 24) | (b1 << 16) | ...`, which also
exposed a counter bug (fixed: `residual_classes._extension_kinds` now counts only `sll`+`sra/srl` pairs
and `andi` masks).

## The class

Census (`../structural-residual-20260929/packed_words.py`): 9 unsolved functions pack more words from bytes
than their target; in 8 the target uses `lwl/lwr`, all libultra PIF/controller routines plus
`updateRaceResultsFlow`.

## The compiler rule (probed on IDO, sources here)

| source | -O1/-O2 result |
|---|---|
| `copy.c`: local `Bytes40 l; l = *(Bytes40 *)ptr;` | 4 `lwl`, aligned `sw at`: the target's exact loop (end at +0x24, 3 words/iteration, trailing word) |
| `copy4.c`: 4-byte struct | 1 `lwl/lwr` pair |
| `castdst.c`: `*(Bytes40 *)u32arr = ...` | `lwl` **and `swl`** |
| `aligndst.c`: union member of that type | `lwl` **and `swl`** |
| `packed.c`: byte packing (current candidate form) | 0 `lwl`, 24 `lbu`, 18 `sll` at -O2 |

Catalogued as `ido53-unaligned-struct-copy`.

## Mechanism

`solver/unaligned_copy.py`, wired into `site_edits` as `shape:unaligned_copy`. It handles two m2c shapes: the
loop (osMotorStart) and straight-line stores (__osContGetInitData). It keeps a struct-typed destination,
retypes an array destination to `UnalignedN`, and drops locals only the removed block used. Tests:
`tests/test_unaligned_copy.py` (fire on both motivating residuals, decline without the target
signature, site-search wiring, dead-local removal).

## Results (`run.py`, one compile per variant from each best state; `results.json`)

| function | before | after | lwl/lwr target/candidate |
|---|---:|---:|---|
| osMotorStart | 50.6 | 89.3 | 8/8 |
| osMotorStop | 50.6 | 89.3 | 8/8 |
| __osContRamWrite | 52.2 | 84.2 | 8/8 |
| __osContRamRead | 50.6 | 80.5 | 8/8 |
| __osContGetInitData | 49.1 | 78.2 | 4/4 |
| osContGetReadData | 24.0 | 57.9 | 4/4 |

Silent, each explained: `__osPfsGetInitData` (anonymous multi-line union destination indexed past its
size, m2c residue), `updateRaceResultsFlow` (a different shape: unaligned on both sides, `swl/swr`),
`osPfsFindFile` (target has no `lwl`; the gate declines correctly).

The class-by-class search from these states (`../composed-edits-20260929/`, arms `ucopy` and `ucopy-deep`,
budget up to 160) found 0 exact and stopped early. In osMotorStart the frame is 0x50 against 0x60,
from m2c temporaries homed at -O1, and one width fault the edit set can't touch. Strict class
ordering then blocks every later class. That's the next thing to fix.

# Block-by-block inverse: feasibility (mixed)

Zero compiles. 29,120 (C line -> instruction shape) observations from our own compiled search candidates in 224
functions (no reference source); target blocks of the 208 unsolved functions tiled only with shapes from OTHER
functions, registers abstracted to their class.

| level | blocks fully tiled | instructions in tiled blocks | functions fully tiled |
|---|---:|---:|---:|
| strict (constants/offsets kept) | 14.9% | 3.7% | 2 / 208 |
| loose (constants/offsets abstracted) | 61.5% | 39.0% | 26 / 208 |

Pre-registered bar (>= 50% loose AND >= 25% strict) not met; shelve bar (< 20% loose) not met either: mixed.
Reading: statement STRUCTURE is largely reusable across functions; the constants and offsets are function-specific
(and are what the target assembly states, which evidence_site already writes); large blocks rarely tile, most likely
because as1 interleaves neighbouring statements. A pure lookup inverse will not work; a hybrid (structure from the
known-good C library, constants from the target, compiler decides) is plausible, and its value over m2c's own
per-block decompilation is only the idioms m2c gets wrong -- that needs its own test before building.

# Protocol (gap 4): what each C construct adds to the IDO 5.3 frame

Written before `constructs.py` ran. Replaces round 7's inference-from-object approach, which could not measure the
locals area.

Paired synthetic compiles (game recipe), base = a function that makes a call and keeps one local in a register.
Each variant adds one construct; measured: frame size (`addiu sp,sp,-N`) delta from base.
Predictions (H15): an address-taken `int` adds 4 bytes to the locals area; a `char buf[N]` whose address escapes adds
N rounded up to 4; a 12-byte struct whose address escapes adds 12; two address-taken ints add 8; a fifth call argument
widens the outgoing area by 4; the frame is then rounded up to 8. **Confirmed** if every delta equals
align8(base_raw + added) - base, i.e. the prediction holds after rounding, in all variants.

## Result (constructs.json, show.py)

**H15 not confirmed.** Deltas were +8/+16, not align8(base_raw + added) - base. The sp operands show why:

| variant | frame | ra | memory locals | outgoing |
|---|---|---|---|---|
| base | 24 | 20 | none | 16 |
| addr_int | 32 | 20 | y@24 (= -8) | 16 |
| two_addr_ints | 40 | 20 | y@32 (-8), z@28 (-12) | 16 |
| char_array_8 | 40 | 20 | buf@28 (-12) | 16 |
| char_array_6 | 40 | 20 | buf@28 (-12) | 16 |
| struct_12 | 40 | 20 | t@24 (-16) | 16 |
| fifth_arg | 40 | 28 | x@36 (-4, spill home) | 20 (5th arg @16) |

The register-allocated `x` still owns -4: every declared local takes a virtual offset in declaration order, but only
memory-resident ones (address taken, or spilled to their home) make the locals area deep. The three regions are
rounded to 8 separately. Refinement H15b is pre-registered in PROTOCOL-layout.md.

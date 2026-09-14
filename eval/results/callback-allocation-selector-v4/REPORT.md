# Scratch-cycle transfer probes

Eight further selector relationships were compiled on the98.156 alternative with correct persistent registers. None improved it or reached exactness.

- Repeated u32/u16 narrowing casts:98.156, inert.
- Unsigned left/right shift narrowing:94.629.
- `type &= 0xffff`, including inside the switch expression:96.312.
- Signed preserved/full-type carriers:94.943.
- u32 or s32 parameter declarations followed by the existing explicit u16 mask:97.106.

All eight compiled. No candidate was retained or integrated. These tests did not find a source expression that shifts the temporary-register cycle while preserving the already-correct instruction sequence.

The prior98.156 alternative remains available in `../callback-allocation-selector-v3/00.c` with76/76 verified semantic passes. The better similarity-score champion remains98.511.

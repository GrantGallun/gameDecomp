# Protocol: IDO 5.3's stack frame size

Written before `eval/frame_size.py` ran.

## Why
`field:immediate` residuals at the unsolved best nodes are 77% frame size (`addiu sp,sp,-0x18` vs `-0x20`: 111 of
145 instances, 50 functions). The source states no frame size; it follows from what the function needs.

## H11 (prediction, fixed now)
frame = align8(A + 4 * saved_int + 8 * saved_float + 4 * memory_locals), 0 means no frame, where
- A = 16 (the o32 outgoing-argument area) if the function makes any call, else 0;
- saved_int = distinct `sw sN|ra, k(sp)` in the target prologue; saved_float = `sdc1/swc1 fN, k(sp)` there;
- memory_locals = uopt isvar kind M nodes of the procedure none of whose ranges was coloured (level-5 trace).
Measured on every census procedure with a target dump. **Confirmed** if >= 90% of functions match exactly;
otherwise the residual (target - predicted) is tabulated for the next hypothesis.

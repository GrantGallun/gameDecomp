# Protocol: is a member access interchangeable with an explicit byte-offset access on IDO 5.3?

Written before any compile in this directory.

## Why
`field:offset` is the top buildable residual (roadmap 2026-09-23b: blocker weight 10.75, 58 functions uncovered).
The diff states the target offset, but in most cases the source spells the access as a struct member, often of
a header type the candidate cannot edit, so no mechanism acts. If IDO compiles `p->m` and
`*(T *)((u8 *)p + OFF)` identically, the stated offset becomes a local edit with no layout change.

## H10
For T in {s32, s16, u16, s8, u8}, access in {load, store}, base in {pointer parameter, pointer local, element of a
global struct array}: a synthetic function using `p->m` (m of type T at offset OFF) and the same function with
that access written `*(T *)((u8 *)p + OFF)` produce identical objects (sections and relocations).
**Confirmed** if all 30 pairs are identical. Any differing pair is recorded with its disassembly and the rule is
reported only for the cells that held.

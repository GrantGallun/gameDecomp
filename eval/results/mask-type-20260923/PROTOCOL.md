# Protocol: do narrow local types produce the surplus masks?

Written before h13.py ran.

`extra:andi` (roadmap 2026-09-23b: 68 functions uncovered): the candidate has an `andi 0xff/0xffff` the target
lacks, and the attributed line has no `& 0xFF`/`(u8)` for evidence_site to remove.

**H13.** For a local assigned an int-valued expression and then used in arithmetic, declaring it `u8` (`u16`)
makes IDO 5.3 emit `andi 0xff` (`0xffff`) that declaring it `s32` does not; and `s8`/`s16` emit a sign-extension
pair (`sll`/`sra`). Paired compiles over 4 shapes (assigned from a sum, from a call, from a load of a wider field,
incremented in a loop). **Confirmed** if in every shape the narrow unsigned declaration has exactly one more
`andi` than the `s32` one and the objects otherwise agree in instruction count within that andi.

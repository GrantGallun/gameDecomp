# Result: width residuals, localized: null by the registered criteria

`linemine.py capture` (500 pairs recompiled with line capture; 4 failed, counted) -> `linemine.py analyze` ->
`analysis.json`. Protocol: `PROTOCOL.md`.

## Registered outcome
- Events: 4,485 width events in 500 pairs. **Paired: 904 (20%)**; unpaired 3,571 (the line has several primitive
  variables and no common assignment target, or none: struct fields, globals, pointers).
- **Rule candidates that change a type: 0.** All nine candidates (n >= 8, one reference type >= 60%) keep the draft's
  type (`field:lhu` u16 -> u16, `opcode:andi/li` u8 -> u8, ...).
- Positive control: **untestable** (fewer than 8 deduplicated paired `extra:andi` events on u8/u16 lines, and
  fewer than 8 `missing:andi` on s32 lines).
- Type-change rate on paired events: 205 of 513 deduplicated (40%). Most width residuals sit on lines whose
  variable the reference types the same way as the draft.

## Reading
1. The scope estimate (`scope.py`: 319/504 pairs differ in local types, 135 with a width residual) does not
   localize: where the width residual is, the paired local usually has the same type. Function-level co-occurrence
   overstated the lever.
2. Post hoc, not a verdict: when the reference does change a narrow draft local on a shift residual, it widens it to
   s32 (s16 -> s32 in 20 of 27 events on missing/extra/field sll and sra; u8 -> s32 on byte loads). That is the
   existing rule `ido53-narrow-local-mask` (evidence_site retypes a narrow local on a surplus mask or extension to
   s32; population rerun 2026-09-23: 0 gains, 0 losses). Nothing new to build.
3. Pairing is the instrument's limit: 80% of events have no single variable on their line. The remaining width
   faults are mostly on struct-field and global accesses (types factored out in these pairs by construction) or on
   expression shape (casts, intermediate temporaries).
4. Bias: the drafts were produced with the reference's m2c context, so their local types agree with the reference
   more often than assembly-only drafts would.

## Next, if this axis is pursued
Expression-level attribution (the casts and intermediate temporaries on the event's line, not declared types)
would cover the 80% this pass could not pair. It is not planned here.

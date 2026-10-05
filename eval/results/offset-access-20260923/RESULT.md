# Round 4: stated offsets written as member accesses

**H10 confirmed, 30 of 30** (`h10.json`): IDO 5.3 compiles `p->m` and `*(T *)((u8 *)p + OFF)` identically for
s32/s16/u16/s8/u8, loads and stores, pointer parameter / pointer local / global struct array element.
`solver/evidence_site.py` now applies a stated offset to a member access on the attributed line when no literal
matches (T from the access opcode). Catalog: `uopt53-member-offset-equivalence`.

Replay at the unsolved best nodes (`replay.py`): 175 candidates in 45 functions, 164 compiled, 23 improved (13%;
base 5%) in 14 functions, 0 exact; largest __sinf +4.73 (fire test fixture). Offsets come in clusters from one
misaligned layout, so fixing one access moves a function little.

Population rerun (`../member-offset-population-20260923/`, code-v6 vs index_form): 15 vs 15 exact, 0 gains,
0 losses; unsolved 8 better, 4 worse. Kept (acceptance: no exact lost). Round outcome: 0 new exact, 1 new
confirmed rule.

## Round 6: width and signedness at a header member
`evidence_site`'s opcode path retypes a type token or declaration on the attributed line; when the access is a
member of a type the source does not declare, it now writes the access at the target's width and the same offset
(uopt53-member-offset-equivalence). Replay: 7 candidates, 2 improved (setRaceCameraModeForced +10.26).
Population rerun (`../width-population-20260923/`, code-v7 vs member_offset): **16 vs 15 exact, 0 losses**;
gain **initRaceCameraChase** (source-independent, SOLVED 274): `D_801124A0->timer = 0x96;` ->
`(*(s16 *)((u8 *)(D_801124A0) + 0xA0)) = 0x96;` (header declares `timer` 32-bit; the target stores a halfword).
Round outcome: 1 new exact.

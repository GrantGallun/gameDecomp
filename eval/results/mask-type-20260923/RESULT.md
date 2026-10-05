# Round 8: surplus masks from narrow locals (H13 partial)

H13 (`h13.json`, paired compiles, s32 vs u8/u16/s8/s16 local): from a call, exactly one extra `andi` (unsigned) or
`sll`/`sra` pair (signed); from a sum, the mask plus one more instruction; from a wider field load, no mask (IDO
narrows the load: lbu/lhu); as a loop counter the loop compiles differently. The pre-registered criterion held only
for the call shape: **partial**. Catalog `ido53-narrow-local-mask`.

`evidence_site`: a surplus mask/extension on a line assigning a u8/u16/s8/s16 local retypes that local to s32.
Replay: 11 of 16 candidates improved (9 functions; drawMenuAsciiTextDefaultScale +12.94, fire test). Population
rerun (`../narrow-population-20260923/`, code-v8 vs width): 16 vs 16 exact, 0 gains, 0 losses, 6 better / 1
worse. Kept. Round outcome: 0 new exact, rule partial (not counted as confirmed).

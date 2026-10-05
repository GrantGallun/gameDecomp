# Paired population trial: at_inline + evidence_site symbol addends (code-v14)

Written 2026-09-24, before any compile in this directory, while the code-v13 trial (counter-loop-20260924) ran.

Changes:
- `branch_shape.at_inline` (family `at_inline`): m2c's `var_at` variables folded back into the condition they test.
  Rule: `at` is the assembler temporary and never homes a C variable, so `var_at` is always a decompiler artifact.
  Fire tests on the 13 unsolved functions carrying one: MusStop, MusHandleSetFreqOffset, MusHandleSetPan and
  MusHandleSetVolume reach 100.0; MusHandleStop 82.6 -> 98.6, MusHandleSetTempo 83.1 -> 89.4, Fstartfx 66.4 -> 71.9;
  findRaceItemProjectileHomingTarget 78.4 -> 77.7 (worse); the rest are small changes.
- `evidence_site` silent decline fixed: relocation operands dropped the addend, so `%lo(G+4)` equalled `%lo(G)` and a
  symbol-addend residual produced no class at all. Now it is a `field:symbol` / `field:offset` class, and the edit reads
  G at the target's addend (`*(T *)((u8 *)&G + N)`). Fire test: resumeGameTask 99.688 -> 100.0. Three unsolved
  functions carry this class.

Code: code-v14 = code-v13 + overlays `solver/branch_shape.py` and `solver/evidence_site.py`.
Runs (budget 32): 1. the 224 frozen sources, paired against code-v13's run 1; 2. round 4 from the same round-3 starts,
paired against code-v13's round 4.
Acceptance, fixed now: keep the changes only if run 1 loses 0 exacts against code-v13. The evidence_site change also
changes the roadmap's class counts (addends are now visible); that is intended.

## Result (`analysis.json`)
Run 1: 27 exact against 23 for code-v13, **0 losses**; gains MusHandleSetFreqOffset, MusHandleSetPan, MusHandleSetVolume
(at_inline, then frontend_type) and resumeGameTask (evidence_site addend). 7 improved (MusHandleStop 82.6 -> 98.6,
MusHandleSetTempo 83.1 -> 94.4), 1 worse (updateCloseRangeHomingItemProjectile 78.2 -> 77.7). Run 2: 13 against 8, the
same four plus MusStop, 0 losses, 8 improved, 0 worse. at_inline 14 edges / 13 functions, none broke the build.
Recorded (ratchet-checked): receipts 95900-95904; the KB went 388 -> 393. The 386 -> 388 in between was another session's
run (register-o1-inventory-20260924: __osResetGlobalIntMask, __osSetGlobalIntMask), not this trial.
**Decision:** acceptance met; both changes stay.

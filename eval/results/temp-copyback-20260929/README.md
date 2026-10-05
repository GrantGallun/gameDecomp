# Three residual classes found by reading, one chain, two exact

2026-09-29. It continues `../unaligned-copy-20260929/`. Each class was found by reading the residual left after the
previous repair on osMotorStart, confirmed on IDO before any code was written, and generalised only after that.

## Classes and rules

| class | catalog | evidence | generator |
|---|---|---|---|
| m2c copy-back temporary homed at -O1 | `ido53-o1-copyback-temporary` | `m2c.c` vs `direct.c` vs `forloop.c` at -O1: the temporary is stored and reloaded | `solver/temp_copyback.py` |
| constant-index array element of a local at -O1 | `ido53-o1-array-element-base` | `../unaligned-copy-20260929/field_array.c` (80-byte frame, `andi s0`) vs `field_named.c` (64, direct `lbu`) | `UnalignedN` got named fields in `solver/unaligned_copy.py` |
| rotated counted loop vs `for` | `ido53-o1-counted-loop-shape` | `loopshape.py` on osMotorStart at 99.223: `for` 100.0, five other spellings 94.7-99.4 | `solver/counted_loop.py` |

All three are wired into `site_edits` as `shape:*` edits. Tests: `test_temp_copyback.py` (fire, two
declines, gate), `test_counted_loop.py` (raw-draft fire, reads-before-increment, decline),
`test_unaligned_copy.py`, `test_residual_classes.py`.

## Measurement fixes along the way

- `residual_classes.frame` is a class of its own (after width), and `operand` excludes `sp`-relative
  constants. Before this, the copy-back merge (registers 22 → 6) was rejected for an "operand" fault that was only
  a frame offset.
- `counted_loop` first matched only `i = i + 1` as the loop's first statement and fired on 0 of 843 unsolved
  best states. Real drafts increment anywhere and through temporaries. The generalised version fires on 18.

## Greedy chain (`greedy_chain.py`, from each function's best ledger state, results in `greedy_chain.json`)

Prediction written in the script before the run: osMotorStart/Stop exact from raw drafts; 0-3 others.

| function | before | after | steps |
|---|---:|---:|---|
| **osMotorStart** | 50.6 | **exact** | unaligned_copy, counted_loop, temp_copyback (9 compiles) |
| **osMotorStop** | 50.6 | **exact** | same |
| __osContRamWrite | 52.2 | 89.4 | copy, loop, copy-back |
| __osContRamRead | 50.6 | 88.1 | copy, copy-back, loop |
| osContGetReadData | 24.0 | 67.2 | copy, loop |
| updateCourseSelectExtraCourseIconListClose | 66.0 | 81.9 | loop |
| osPfsChecker | 71.2 | 77.6 | 5 copy-backs, loop |
| updateCharacterSelectUnlockedCourseList | 87.3 | 90.0 | loop |
| alFxPull, __osPfsGetInitData, updateCourseSelectCourseIconList | | +0.3 to +2.8 | loop |
| 8 others | | unchanged | no improving variant |

Both exacts pass `workspace.repair_complete` (object bytes and frontend). They were unsolved in both
ledgers. They start from campaign drafts that include `common.h` and libultra internal headers, so they are
**header-assisted development results**, not capability numbers. The sources are only in
`/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite` (run_kind `greedy-chain`), not recorded in
a ledger.

# Remaining failure modes: census and fixes (2026-09-14)

## Census (checkpoint 16986, read-only)

`census.py` → `census.json`, `pending.json`:
- **1135 pending:** 1027 compile and pass the frontend, 96 do not compile, 12 are frontend-rejected.
- **Closeness by total instruction faults:** 86 near (≤4), 170 close (≤12), 318 mid, 453 far.

`diff_pass.py` compiled all 256 near and close functions in isolated benches (`diffs/`). `classify.py` →
`classes.json` labels each aligned non-register difference by mechanical cause, with branch offsets masked:
- **Single-cause groups:** 41 register-only, 13 immediate, 10 literal-where-symbol, 9 rodata-where-named, 8 move,
  8 verification-only, 119 multi-cause.
- **Most frequent structural labels in the multi-cause group:**
  - `andi`→`li` (9);
  - an extra `lh` (10);
  - a missing `move` (11);
  - `slt`/`bnez` reshaped to `sltu`/`beqz` (6).

## What each mode was, and what was built

| mode | root cause | fix | measured |
|---|---|---|---|
| ROM asset addresses | m2c passes `(void *)0x593D10`; IDO loads a literal with `lui/ori`, a symbol with `lui/addiu %lo` | `solver/address_symbols.py`: address read from the object diff, spelled as the link-defined segment bound `&_593D10_ROM_START` | 27 functions with 304 sites: 4 exact (`D_` spelling), 22 improved; loadRaceCourseAssets 33.9 → 72.9 |
| stale certificate refusals | score-100 nodes kept schema-1 refusals from before the 2026-09-13 certificate amendment | `recertify@<certificate digest>` profile | 5 of 5 function-exact on re-score |
| rodata / hardware verification gap | the ROM-backed certificate refused any object with `.rodata`, and was never asked when normalized text differed only in relocation spelling | certificate schema 3 plus the operand-only certification gate (`solver/function_boundary.py`, `solver/workspace.py`) | 21 newly function-exact (`v3-probe.json`), 9 more with segment names (`v3-probe-segments.json`) |
| chained assignment | `F = 2U; v = 2 & 0xFF;` where IDO emitted `v = F = 2U;` | `structural_mutations.chained_assignments` | 58 improved |
| signed hex comparison | `x < 0xFFD00000` compiles unsigned (`sltu`) | `structural_mutations.signed_hex_compares` | 17 improved, e.g. ending slides 77.7 → 93.2 |
| register locals | libultra keeps `__osDisableInt()` results in `s0`; m2c's `temp_s0` spilled to the stack | `structural_mutations.register_locals` | 14 improved, 1 exact (osRecvMesg); osViBlack 83.7 → 99.3 |
| integration: prototypes | plain m2c prototypes blocked preparation | `prepare_integration`: prototypes become extern declarations, yielding to names the destination TU already has | 73 of 180 blocked exact candidates unblocked (71 object-exact, 2 function-exact) |

All three structural families together (`probe_structural.py`): 89 of 356 improved, 1 exact.

## Whole-ROM integration proofs (`census-deploy-20260914/integrate_v3.py`)

- **ROM-exact:**
  - __osSiRawStartDma: hardware literals;
  - waitForTitleDemoRaceIntroStart: rodata float;
  - initMainMenuSettings, initStartupControllerPakFlow, loadMainMenuSceneModelAnimationBank,
    updateControllerPakReplaySaveMessageFirstPageFadeOut, initMainMenuModeSelect: segment-bound asset names, built
    as one union.
- **Failed, and the fix each failure drove:**
  - drawRaceSplitscreenSelectEntryFee, own string literal: passed the function certificate but failed the ROM
    checksum, so schema 3 now refuses address-taken candidate rodata.
  - initMainMenuSettings with splat `D_` names: undefined reference at link, so address_symbols now uses segment
    bounds.
  - finishTrainingCourse: a prototype conflict, now fixed. It still fails on a function-pointer argument type that its
    m2c prototype had hidden. That is an open integration mode; frontend fix-its at integration time would cover it.

## Measured but not a mode

- **Ungated existing search** (`search-1`): 31 exact of 131. 26 of those are register-only functions the campaign's
  gated search already owns, so broadening the gate is not worth it.
- **`(float)0x…` integer casts:** one function (initRaceTypeSelectMenu). Schema 3 exposed it: the normalized dump
  hides float values, so it scored 99.9 with the wrong constant.

## Still open, in size order

- **Multi-cause structural residuals in the mid and far tiers:** branch shape, extra or missing loads.
- **Local struct and typedef definitions in candidates:** they block integration for 107 exact candidates.
- **Immediate and stack-slot differences:** frame layout.

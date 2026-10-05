# From m2c drafts to near misses: results (2026-09-30)

The session started as "mature the m2c drafts" (loop-shape normalisation, L1). That test was negative. Measuring
where the residual actually sits then led to three levers that pay: running the existing search on near misses,
crossing neutral plateaus, and closing a certificate gap for jump tables. Everything below is in trial databases and
disposable ROM copies. **Nothing has been written to the campaign state or either ledger.**

## Headline

| lever | functions | evidence |
|---|---|---|
| jump-table certificate (schema 3 extension) | **36 newly function-exact, 28 whole-ROM verified alone** | `rescore_fb.jsonl`, `jtbl_integration_dry.jsonl` |
| main-tree site-edit search + mined lane on near misses | **9 exact** (7 of 226 within 10 instructions, 2 of 214 at 10–30) | `near_search.jsonl`, `band_10_30_search.jsonl` |
| plateau search (neutral steps allowed) | **5 exact** of the 219 near misses the site-edit search left | `plateau_near.jsonl` |
| re-score under today's toolchain | 1 (setCurrentGameTaskCallback, known from 9/29) | `rescore.jsonl` |

**Provenance correction (`provenance.py`, `provenance.json`).** The starting sources matter. **31 of the 36 jump-table
candidates are `authorized-target-history-recovery` or `historical-provenance-exact-source`:** they were recovered from
the target repo's history, which is the reference, and belong to the *recovered* tier, not capability. Here is the
split by whether the starting source was recovered:

| route | not recovered | recovered |
|---|---|---|
| jump-table certificate, whole-ROM verified | 3 (handleRaceSplitscreenSelectFlow, runTrainingCourseUntilLessonEnd, updateShopMenuModeCursor) | 25 |
| jump-table certificate, blocked or failed | 2 | 6 |
| site-edit search exact | 4 (FrandVolume, __osSumcalc, drawMenuSprite, repairControllerPakId) | 5 |
| plateau search exact | 5 (4 new plus setCurrentGameTaskCallback) | 1 |

"Not recovered" is not yet "clean". Several start from sources that include reconstructed `include/game` headers,
which is the header-assisted tier. `eval.status` decides the tier. The certificate fix is sound either way. The capability claim is the
right-hand-column-free count: 3 + 4 + 4 new exacts.

Every one of these was unsolved by the campaign's definition (no own exact in either ledger, not integrated or
pending integration). The sealed held-out 50 are excluded from every frame (see the contamination note).

## 1. Loop-shape normalisation of drafts (L1): negative

Pre-registration: `PREREGISTRATION.md`. Code: `solver/c_stmt.py`, `solver/loop_shape.py`, `solver/skeleton.py`.
Tests: `tests/test_loop_shape.py`.

| prediction | result |
|---|---|
| P1: variants for at least 70% of goto drafts | 100 of 185 = 54% ✗ |
| P2: skeleton strictly better in at least 30% | 3 of 173 = 2% ✗ |
| P3: gradient strictly better in at least 25% | 8 of 173 = 5% ✗ |
| P4: at least 1 loops-only exact | 0 ✗ |

IDO compiles m2c's goto loops to the target's branch skeleton anyway. The census correlation between draft shape and
solved status was a confound: complex control flow produces both gotos and hardness.

Also found:
- 222 of the 395 frame functions have no compiling clean binary-type draft. The errors are syntax errors, undefined
  fields, struct casts, and selectors on non-struct pointers. Later stages (m2c_byte_view, compile-fix prompts) handle
  these.
- The census counted m2c's unknown-type marker `? arg1` as a ternary. Fixed in `eval/draft_census.py`. The ternary
  row of `../draft-census-20260930/RESULTS.md` is inflated until the census is rerun.

## 2. Where the residual is (`best_skeletons.py`, `near_miss.py`)

Of 880 unsolved functions with a compiled attempt, the best candidate already has the target's branch skeleton in 665
(76%). 244 are within 10 instructions (92% skeleton-correct), and 39 of those differ only in register names. The
lock is instruction-level, and many functions are one or two notches from open.

## 3. The main-tree search on near misses

The 9/29 site-edit search (shape lane plus the rule miner's mined lane) was never deployed to the campaign. Run on
the near-miss frames (budget 72, default table):

| exact | baseline gradient | how |
|---|---|---|
| FrandVolume | (0, 0, 2) | mined A: `x = x + y` → `x += y` |
| drawEndingCreditsTumblingSnowboard | (0, 2, 0) | mined B template |
| spawnEndingCreditsPhaseAdvanceSparkle | (0, 5, 0) | mined B template |
| spawnEndingCreditsDelayedSparkle | (0, 5, 0) | mined B template |
| initGhostSlowdownActor | (0, 1, 15) | type edit |
| drawCharacterSelectCourseExitPreviewPanel | (0, 4, 0) | pool: relocation |
| __osSumcalc | (0, 6, 8) | shape: counted_loop |
| repairControllerPakId | (0, 12, 13) | mined A: copy-back temporary |
| drawMenuSprite | (0, 13, 23) | mined B template |

These are the rule miner's first exacts. On random frames (T4/T5) it only moved gradients; on near misses it closes
functions. Also fixed: `site_edits.search` returned `"exact": False` when the starting source was already exact.

## 4. Plateau search (`solver/plateau_search.py`)

A greedy search keeps only improving children, so a key that needs two individually neutral edits is unreachable.
Found by hand on loadRaceMotionJointAnimationFrame, register distance 1: inlining the call and swapping `ptr + off` to
`off + ptr` are each (0, 0, 1), and together exact. The plateau search is best-first over (gradient, plateau steps)
and admits up to 2 tying steps. Every generator family is round-robined, because sorting by residual weight alone let
`decl` retypings take 139 of 240 compiles. Finished on the 219 near misses the site-edit search left, 47,345 compiles in all. 5 new exacts, and 17 more improved:

| exact | baseline gradient | compiles | final edit |
|---|---|---|---|
| updateEndingCreditsCharacterAura | (0, 0, 2) | 197 | field_local: compound + unmasked |
| updateEndingTommyHopLeftToPhase0A | (0, 0, 2) | 53 | field_local: compound |
| updateEndingLindaHandshakeAnimComplete | (0, 0, 5) | 3 | single_use: inline |
| FrandPan | (0, 4, 3) | 65 | commutative swap |
| updateTrainingCourseLessonEndMenu | (0, 6, 24) | 183 | decl: u16 → s32 |

setCurrentGameTaskCallback was exact at baseline. The per-function results do not keep the path, so the neutral
steps are not separated from the broader generator set (regalloc_mutations plus rewrite_library on top of the
site-edit lanes). Three of the five are register-only functions where regalloc search had failed before.

`solver/nearmiss_llm.py` adds a local-model generator (gpt-oss:20b, our candidate plus the oracle diff only, with the
contamination guard) as an opt-in plateau family. Smoke test: 6 of 8 of its edits only added comments or parentheses,
and these are now filtered out. Not yet measured.

## 5. Jump tables: a certification gap, not a decompilation gap

32 unsolved functions' best candidates were instruction-identical to the target except the jump-table relocation
(`%hi(jtbl_800E0B00)` vs `%hi(.rodata)`). The ROM-backed function certificate refused any relocated rodata. Schema 3
now admits a jump table under these conditions:
- Every rodata relocation, in both objects, is an R_MIPS_32 against the object's own `.text`. It is read straight from
  the ELF, because asm-processor leaves the target's `.late_rodata` non-ALLOC and the object image never saw it.
- Every entry lands word-aligned inside the certified function.
- The candidate reads the table with `lw` from a site whose target counterpart reads the target's table.
- The linked table words equal the ROM's.

Negative controls (`tests/test_function_boundary_jump_tables.py`): an entry moved by one instruction, and an entry
outside the function, are both refused. All 98 function-boundary-related tests pass.

Re-scoring all 832 unsolved best candidates: **36 are now function-exact** (34 with a jump table). Integrated one at a
time into a disposable game copy (`jtbl_integration_dry.py`, the campaign's own `prepare_integration` and
`integration_gate`):
- **28 rom_exact, whole ROM verified.**
- 7 blocked: "needs shared declaration integration", a preparer limit already on the integration long tail.
- 1 build_failed: checkMainMenuSecretCode.

## Contamination note

loadRaceMotionJointAnimationFrame is in the sealed held-out 50. I read its diff and probed it by hand while
diagnosing near misses. It must be excluded from any future sealed-50 comparison (49 remain). All run frames here
exclude the sealed 50. updateShopMenuSelectedModePanel (sealed) also has a jump-table-only residual. It was not
probed or run.

## To land these in the campaign (needs owner approval)

- An amendment with `solver/function_boundary.py` (jump tables). The integration sweep then takes the 28, plus
  whatever the search finds, through the normal union gate.
- An amendment deploying the 9/29 site-edit lanes and rule miner, with `plateau_search` as a profile for near misses.
- Or record the 11 trial-DB exacts directly, which is on hold per the owner.

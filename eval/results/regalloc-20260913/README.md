# Register-allocation residuals: signatures, gradient bench, mutation search (2026-09-13)

Tooling map: `PIPELINE_MAP.md`, section "Register-allocation signatures, gradient bench and mutation search".

**Cohort.** `cohort.json`, frozen at checkpoint 14495. It holds 78 pending functions whose only residual fault was
register allocation. The campaign's model repair had left them at +-0.

**Results.** Every "exact" below is the object oracle (`workspace.score`), on an isolated workspace, with no model
and no reference source.

| run | generators | exact / 78 | compiles |
|---|---|---|---|
| search-1 (preregistered, `PREREGISTRATION.md`) | local_type, commutative, decl_order, inline_temp, stmt_order | **6** (5 counted; makeFixedRotationXY was found by hand during development) | 1,326 |
| search-2 | + const_inline, stmt_move, plateau moves | stopped early (2 exact in its first 7 functions) | — |
| search-3 | + field_local (first version) | stopped at 55/78 with 22 exact | — |
| **search-4** | + field_local spellings, struct_copy, store_loop, guard_before_load, single_use | **55** | 5,019 |

search-1 scored 5 counted exact, which falls in the preregistered "partial" band (5–14) and short of its threshold of
15. Searches 2–4 are development runs on the same cohort. Each new generator came from a hand investigation of an
unmatched function with `regalloc_probe probe`, so the 55 is **not** a held-out number. It shows these residuals
close, not that the generators transfer to unseen functions. The next check is the 145 functions in
`regalloc_plus_le2`, which drove none of this development.

Hand-verified with the probe and not yet reached by search: randomNextObject (`field++; return table[field];`,
which needs two steps).

**Exact by family (search-4).** field_local 40, stmt_move 4, struct_copy 3, local_type 3, commutative 3,
store_loop 1, decl_order 1.

**What the failure modes turned out to be.** Nearly all were m2c artefacts that change IDO's temporaries and
colouring, rather than allocation-priority subtleties:

- **Field-caching local.** For example `var = F + K; F = var; ... use(var)` where the original was `F += K;` and
  `use(F)`. There are four original spellings, and IDO tells them apart: compound, plain, `F++;`, and `++F`
  inside the condition with m2c's width mask removed. Reads must keep m2c's read cast, such as `*(u16 *)`
  against a `*(s16 *)` write.
- **Split struct copy.** Three s32 word copies where the original was a struct assignment. The tell is IDO's
  `lw at` / `sw at` copy idiom.
- **Flattened constant loop.** Four indexed stores where the original was `while (i < 4)`. IDO unrolls it, and a
  `for` loop rotates the store order.
- **Guard after load.** `v = G; if (v == H) return` where the original tested `G` first, with the `==` operands
  reversed.
- **Narrow local widening.** An `s16` local multiplied with `s32` values reorders `multu` operands. It isn't the
  C operand order: swapping the operands is inert.
- **Constant store order.** m2c's `tmp = 2` indirection hid a store that preceded a decrement.

**Family measurements (search-4 variants).**
- commutative: 1,519 of 1,577 inert. IDO canonicalises operand order.
- stmt_move: 2,410 of 2,465 worse. It is still needed for 4 matches.
- stmt_order (existing): 150 of 163 worse, 0 exact.
- field_local: 40 exact from 180 variants.

## Closing the rest (2026-09-13, continued)

Every closed function was re-verified from its saved source in a fresh isolated bench (`verify_closed.py`, which
writes `closed/index.json` and `closed/<function>.c`).

| status | count |
|---|---|
| **object-exact** (byte certificate) | **74 / 78** (55 from search-4, 19 from hand probes) |
| byte-identical, pending ROM verification | 3 |
| parked (stack layout, not allocation) | 1 |

Each hand closure became a generator with a fire test on its motivating function. Only the source spellings were
found by hand.

**Additional failure modes found and closed:**

| failure mode (what m2c wrote → what matched) | generator | motivating function |
|---|---|---|
| guarded load-modify-store through a declaration-initialised local | `load_modify_store` | updateRaceUiCourseRecordRevealFinalMoney, …MakeBonus |
| field local whose declaration still shifts stack slots; mask kept in later uses | `field_local` +keep_decl / +unmasked | updateEndingSlashStartFinalPose, LindaHop, LindaBlink, SlashAfterVanishWait |
| field local on an extern global | `field_local` (globals) | releaseRelocatableHeapBlockMetadata |
| rotated loop `if (C) { for (;;) { …; if (!(C)) break; } }` | `rotated_loop` | _collectPVoices |
| load cached in a user local while the stored sum had none (priority inversion) | `readonly_field_local` + `store_value_local` | Fdrums |
| `L = (T)(L + E)` where the original was `L += E` | `compound_assign` | updateRacePlayerShockEffect, func_800628DC |
| `t = F; F = t + E(t)` where the original was `F += E(F)` | `self_update` | updateRaceCameraFixedPositionFollow |
| byte arithmetic `(I * K) + TABLE` over a typed table | `typed_index` (address and read forms) | initTitleMenuSparkle, initRaceUiPrizePayout |
| **double scaling** `*(&SYM + I * K)` on a typed extern (a behaviour bug in m2c's C) | `symbol_scale` | initRaceCourseScrollingTexture, initCourseBillboardMarker |
| negation folded into the multiplier `E * -K` | `negative_scale` | updateRaceCameraIntroPan |
| return expression whose value belonged in a local | `result_local` | _doModFunc |

**Not closable by object comparison from C, with verified reasons:**
- **`__osSpGetStatus`, `osAiGetLength`.** The constant-address read (`*(u32 *)0xA4500004`) is
  instruction-identical to the target. The target object names that address through a relocation to a register
  symbol, which none of the C spellings tried (scalar, array, address cast, volatile) reproduces. The ROM bytes are
  identical, so these close through whole-ROM verification.
- **`finishMainMenuDemoRaceIntro`.** All 35 instructions match. The target object has 4 trailing alignment nops from
  its translation-unit layout (.text 160 bytes against 144), which is the known compiler-padding case. It closes
  through isolated integration or whole-ROM verification.
- **`drawCourseSelectExtraCourseBadge`** (parked). Every register matches. One `u16` stack slot is at 0x36 instead
  of 0x34, and declaration order, unused locals of several widths, array and struct wrappers, and removing m2c's
  spill local all left it unplaced. This is a stack-layout residual.

**Transfer check.** `transfer-1/` runs the search-4 generator set, frozen before any hand closure above, on the
untouched `regalloc_plus_le2` cohort. Final result: **63 of 145 exact** (43%), 43 improved, 37 without gradient
progress, and 17,396 compiles. field_local produced 61 of the 63.

Two functions errored because I deleted the run's live `/tmp` bench directories during cleanup. They were rerun in
`transfer-1-rerun/` and both went exact through field_local. That rerun used the later, larger generator set, so it
is not part of the frozen-set measurement. Counting them gives 65 of 145.

**Reproduction check.** `search-7/` re-ran the search with every generator on all 78 functions.
- It reached **73 exact unaided**, in 1,806 compiles against search-4's 5,019. That covers all 55 search-4 closures
  and 18 of the 19 hand closures.
- The one it missed is randomNextObject. Its two-step fix (`field++`, then inlining the masked index local) passes
  through a worse gradient, so the beam drops it.
- It found nothing beyond the verified index.

### Earlier snapshot: what search-4 left unmatched (23) Fault classes: temp_numbering 8, temp_vs_variable 8, variable_colour 3, saved_order 1,
float registers 1, commutative_swap 1, non-register 1. See `search-4-analysis.json` → `per_function` and
`unexact_top_substitutions`. These include:
- audio and libultra code (`_collectPVoices`, `_doModFunc`, `Fdrums`, `__osSpGetStatus`, `osAiGetLength`), which is
  possibly built with different flags;
- larger temp-numbering residuals (`initCourseBillboardMarker`, `updateRaceCameraIntroPan`);
- 4 ending functions and 2 race-UI functions that are 2 register instructions away.

**Files.**
- `searchN/`: per-function compile logs, `*.exact.c` and `*.best.c`, `summary.jsonl`.
- `searchN-analysis.json`: `analyze.py` output.
- `manual/`: hand-probe variants.
- `dump.py`: side-by-side target/candidate listing.
- `export_cohort.py`: the cohort freeze.

The exact sources are not in the campaign. Promoting them needs the normal amendment and integration path.

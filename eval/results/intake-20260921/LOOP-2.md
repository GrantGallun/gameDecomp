# Iteration 2 of the loop: the instrument first, then the blocker it revealed

Two changes, in the only order that would have worked. The measurement was capped, so the priority
list was wrong; fixing the cap moved the target, and the new target was the largest single gain the
frame has recorded.

| receipt | IDO compiled | IDO+frontend | byte-exact |
|---|---:|---:|---:|
| `globals` (end of LOOP-1) | 38 | 21 | 2 |
| `ferrorlimit` / `ferrorlimit2` (instrument only) | 38 | 21 | 2 |
| **`smi`** (`scalar_member_index` wired) | **47** | **28** | **3** |

**9 gained, 0 lost**, 469s. Full account of the instrument half: [ERROR-LIMIT.md](ERROR-LIMIT.md).

## 1. The class set was a 20-error window — a third one

`FAULT-HISTOGRAM.md` fixed the runner's six-line window and `errors[:40]`, and was still measured
through **clang's own `-ferror-limit`, which defaults to 20**. `solver/frontend_check.recipe` passed no
such flag. In the `globals` receipt the highest error count ever observed is **19**, **352 of 780 reads
sit exactly there**, **118 of 200 states** have a pinned read, and **`errors_truncated` is 0
everywhere** — the `fatal error: too many errors emitted` notice is a `kind` that `_ERROR` never
matched, so the cut left no trace.

Acceptance did not move (38/21/2 → 38/21/2), which is the check that this was an instrument change.
`max errors observed` went **19 → 278**. Fixing it also exposed the mirror defect:
`classes_from_window` was labelled off the 16 kB *text* cut, flagging 83 complete class sets as
windowed; it reads `errors_truncated` now and the trace carries both cuts apart (83 → 0).

## 2. The ranking that changed, and the owner that did not exist

Ranked by **sole blocker** — states one class from compiling, which is the tractability rule rather
than the volume rule — `member-on-typed-pointer` came out first at **11 sole, 74 total**. Under the
ceiling the same class read as a depth-1 curiosity cleared in 3, which is why nothing was aimed at it.

Three modules touch that diagnostic and none owned the shape:

| module | gated on |
|---|---|
| `solver/void_field_repair` | base type `void` **exactly** |
| `solver/negative_field_repair` | the diagnostic, but proposes only for **negative** offsets |
| `solver/m2c_negative_offset` | the `base->unk-N` spelling |

So `p->unk10` with `p` of type `s32 *` — non-void, non-negative — had no owner at all.

### The repair invents nothing

`solver/scalar_member_index.py`: `p->unkN` on a scalar base becomes `p[N / sizeof(T)]` — the index
form of the **same byte offset**. No field name, no struct, no layout, so CLAUDE.md's fifth invariant
is satisfied by construction rather than by argument.

```
u8  *p;        p->unk1            ->  p[1]          drawMenuAsciiTextDefaultScale
s32 *q;        (q + n)->unk10     ->  (q + n)[4]    getRaceCourseTargetPositionAhead
short a[64];   gAssetHandles.unk2 ->  a[1]          selectMenuRenderScratchBuffer, via the aka
```

The base type and the exact site are **the checker's own words**: clang names the type and points its
column at the `.` or `->` itself (verified against clang 20.1.2). That is what makes it safe on bases
that are arbitrary expressions, and it is why this needed the error-limit fix first — cfe stops at the
first error, so on most of these candidates the member-reference lines were not in the text at all.

Declines, each named: `void` bases, negative offsets, and offsets that are not a multiple of the
width. The last one is real on this frame — `(gRaceCourseSurfaces + sp34)->unk12` on `s32 *`, where
0x12 is 18 and 18 % 4 is 2, so an index would be a layout **choice**.

### Measured on the 11 in isolation, before wiring

7 frontend-clean, 8 IDO-compiling. The 3 that did not are the deliberate declines waiting on their
existing owners — a `void` base in `initMenuAsciiFontTexture`, a `->unk-2` in `multiplyFixedMatrix3s`.
That is the composition signal, and it is visible only because declines carry a reason.

### On the frame

Wired after `or_address` in `SEQUENCE`, because the rewrite is derived from the declared type and the
declaring passes have to run first. Fired on 37 states, declined on 163.

Newly IDO-compiling: `selectMenuRenderScratchBuffer`, `writebackMenuRenderScratchBuffer`, `Fvibdown`,
`Fvibup`, `drawMainMenuModeDescriptionPanel`, `Fenvelope`, `__MusIntProcessContinuousPitchBend`,
`__MusIntProcessContinuousVolume`, `drawMenuAsciiTextDefaultScale`.

## The new byte-exact match is HEADER-ASSISTED, not binary-only

`writebackMenuRenderScratchBuffer` went byte-exact. It must **not** be quoted as a SOLVED capability
number: the declaration the repair was derived from is `typedef AssetHandle AssetHandles[...]` in
`include/game/engine/asset_manager.h`, a **reconstructed game header**, so the element width came from
the decomp team's own answer rather than from binary evidence.

This also lands exactly on the gap `eval/status.py` already documents about itself: `header_assisted`
keys only on the strategy string `project-header` and is **blind to a candidate that reaches the same
reconstructed headers by `#include`-ing them**. This match arrives by that route. It needs tier
classification before it is counted.

## Tests

| file | n | what |
|---|---:|---|
| `tests/test_frontend_error_limit.py` | 5 | the ceiling, against real clang, and both directions of the window label |
| `tests/test_scalar_member_index.py` | 12 | fires on each real shape from the frame; declines on void, negative, non-multiple, unknown width |
| focused regression | **129 passed, 2 skipped** | every `test_frontend*`, `test_intake*`, `test_fault*` |

One existing test was **rewritten rather than deleted**:
`test_frontend_diagnostics.test_a_class_set_is_labelled_when_the_checker_text_was_cut` asserted the old
proxy. Its intent was right and its mechanism was not, and its stub — `error_count` 9 against a
one-entry list with `errors_truncated: False` — is not a state the real runner can produce.

## Next iteration, ranked from `wide-intake-smi`

| class | states | sole blocker |
|---|---:|---:|
| `undeclared-identifier` | 95 | **10** |
| `undeclared-function` | 34 | **8** |
| `incompatible-int-pointer` | 50 | 4 |
| `incomplete-definition` | 49 | 4 |
| `member-on-typed-pointer` | 53 | 2 *(was 11)* |

Two leads, both with mostly ONE-error states:

1. **`undeclared-function`, 8 sole and 6 of them a single error** (`__ll_div`, `__ll_mod`,
   `__osViSwapContext`, `updateBouncingItemProjectile`, `updateRaceUiResultsBannerWaitForInput`,
   `updateCloseRangeHomingItemProjectile`). One extern prototype each, and the KB `functions` table
   has the names. This is LOOP-1's `known-function` bucket, now measured as sole-blocking.
2. **`m2c_dialect` is worth wiring after all.** `alSynSetPan` (1 error) and `alSynSetVol` (2) are now
   sole-blocked by `undeclared-identifier`, which is what `use of undeclared identifier 'bitwise'`
   classifies as. The action is built and tested (`tests/test_intake_dialect.py`) and was left out of
   `SEQUENCE` because its yield was 0 *at the time* — the other blockers on those states have since
   been cleared, so the residual moved underneath it. A zero-yield pass is worth re-testing after the
   frame moves, not retired.

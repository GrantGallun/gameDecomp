# Two admission routes, measured — and the class neither of them owned

**Date:** 2026-09-17 · **Model calls:** 0 · **Ratchet: 299 → 303 byte-exact, 233 → 237 SOLVED**

This round tested whether the two deterministic admission mechanisms actually convert never-compiling
drafts into matches, and found a third, lexical blocker that sits in front of both of them. The
headline is that the *smallest* of the three findings produced all four matches.

## 0. What matched, and why it counts as capability

| function | route | score |
|---|---|---|
| `__osViGetCurrentContext` | `eval/m2c_redraft.py` — m2c re-run on a blank draft | 100.000 |
| `alFxParam` | `eval/m2c_redraft.py` — m2c re-run on a blank draft | 100.000 |
| `resetRenderScratchAllocator` | `eval/m2c_redraft.py` — m2c re-run on a blank draft | 100.000 |
| `loadMainMenuSceneModelAnimationBank` | `eval/placeholder_admission.py` — `?` resolved, then admission | 100.000 |

All four are **SOLVED**, not recovered and not header-assisted: the drafts were produced by m2c from
the binary, repaired deterministically, and no reference source and no model call was involved. Three
of them came from workspaces whose `base.c` was the 79-byte "m2c failed" stub — they were not
near-misses being polished, they were rows with no hypothesis in them at all.

The same two passes also put a set of functions into scoring range for the first time, which is what a
future model-refine loop needs: `resolveAssetTableRelativePointer` 98.0, `__osSpGetStatus` 96.667,
`__osSpSetStatus` 96.667, `guMtxIdent` 97.273, `allocMenuRenderScratch` 87.2, `__osSpRawStartDma`
85.588.


## 1. Header admission buys scoreability, not matches — measured on its best sample

`eval/header_admission.py` (declaring header first, then `zero_token_harvest.repair_chain`) ran over
the never-compiling stratum. Stopped at **112 of 1633**:

| status | n |
|---|---|
| not-compiling | 94 |
| compiled | 17 |
| raised | 1 |

**22% admit rate**, and zero exact. The decisive test is not the rate but the conversion: the four
*highest* scorers are the most favourable sample the run will ever produce, and running
`solver/repair`'s deterministic rungs on their admitted sources moved none of them to exact.

| function | admitted score | after repair rungs |
|---|---|---|
| `__osSiRawStartDma` | 93.214 | 93.214 (no rung better) |
| `MusStop` | 86.361 | **87.889** |
| `__osSetTimerIntr` | 83.393 | 83.393 |
| `MusHandleAsk` | 80.810 | 80.810 |

If the best four do not convert, the population does not. The run was stopped with this reasoning
recorded rather than left to spend ~6 hours and ~6,000 compiles proving the same thing 1,633 times.
Admission's real product is *scoreability*: a draft at 86–93% is eligible for triage and for a future
model-refine loop, and is no longer an invisible row. That is worth having, and it is not a match.

Receipts: `eval/results/header-admission-full-20260917/state.json`,
`eval/results/repaired-admission-20260917/state.json`.

## 2. 66 workspaces had no draft at all, and that was a stale artifact

15 of the admission failures read `Compiled object has no text symbols`. That is not a recipe bug:
`base.c` for those is 79 bytes — an include plus `// file is blank because m2c failed to decompile
function`. `tools/claude` writes that when m2c exits non-zero **and throws the diagnostic away**, so
the reason was recorded nowhere.

Re-running the *identical* invocation against each workspace's own `target.s` with the m2c installed
now, then running the ordinary admission route on the result:

| | n |
|---|---|
| no-draft workspaces | 79 |
| **byte-exact after the route** | **3** |
| compiling but not exact | 11 |
| still not compiling | 47 |
| still refusing m2c (`other` 11, `jump-table` 1, `directive` 1) | 13 |
| raised — `ObjectBackendRequired: TU requires a separate object postprocessing backend` | 5 |

So ~4.8% of the cohort were rows nothing could ever have solved, and four compiles of the admission
route were spent rediscovering it each time. The drafts are stale, not missing. `eval/m2c_redraft.py`
regenerates them atomically, preserving the original blank file in its receipt.

The 5 `raised` rows are a separate harness gap and are NOT counted as failures of the draft: the TU
needs an object postprocessing backend the scorer does not have, so nothing was learned about them
either way. `updateRaceSetup*Menu`, `raceSetupMenuNoop`, `initRaceSetupSaveMenu`.

Receipts: `eval/blank_draft_census.py`, `eval/m2c_refusal_probe.py`,
`eval/results/m2c-refusal-20260917.json`, `eval/results/m2c-redraft-20260917/state.json`.

## 3. The class neither route owned: m2c's `?` type placeholder

`_Litob`'s regenerated draft is 132 lines and cfe stops at line 12:

```
? lldiv(s32 *, s32, s32);                           /* extern */
```

`Syntax Error` + `Empty declaration specifiers`, and **the error list is truncated there** — nothing
after line 12 is ever judged. One unresolved placeholder masks the whole file. This is the class that
the admission taxonomy had already counted and mis-assigned: `Syntax Error` + `Empty declaration
specifiers` was 21 of 65 terminal failures, read as "the draft does not parse", which is true and not
actionable.

Population, measured:

| | n |
|---|---|
| drafts carrying a `?` declaration | **122** |
| logged attempts whose error contains `Empty declaration specifiers` | **1,962** |
| of the 122, exact after the rewrite plus admission | **1** (`loadMainMenuSceneModelAnimationBank`) |
| of the 122, compiling but not exact | 1 (`guMtxIdent`, 97.273) |
| of the 122, still not compiling | 120 |

`solver/m2c_placeholders.py` replaces the placeholder with a concrete type, evidence first (a name the
KB has a width for keeps it), defaulting to `s32`. It is pure, and `tests/test_m2c_placeholders.py`
asserts it **fires** on all four emitted shapes — prototype, local, unknown pointee, parameter — as
well as declining on clean source and on a `?` in prose.

The first version of it silently skipped `? *var_s3;`, because the pattern required the `?` to be
followed directly by an identifier. The sweep then reported "the error moved rather than cleared" on
six drafts where the same line was still there, unrewritten. That is the silent-decline failure mode
again, caught this time by reading the compiler's caret output instead of trusting the summary.

## 4. The composition admits 1 of 122, and the failures advance rather than clear

Resolving the placeholder makes the file *parse*; the header route makes it *typecheck*. Run in
sequence on all 122 (`eval/placeholder_admission.py`): **1 exact** (`loadMainMenuSceneModelAnimationBank`),
1 compiling (`guMtxIdent` 97.273), 120 still not compiling. The errors do not clear — they **advance to
the next defect class in the same draft**:

| function | residual after both routes |
|---|---|
| `__osCheckPackId` | `__OSPackId *temp` — an unknown type name makes the parameter list parse as garbage, *then* `? sp30;` turns out to be used as `sp30.unk0`, so `s32` is the wrong answer for it |
| `__cosf` | `((((bitwise s32) arg0 >> 0x16) & 0x1FF) < 0x136)` — m2c's own `(bitwise T)` cast token |
| `__osContRamRead` | `typedecl` and `globals` both fire; still not compiling |

Two facts worth carrying forward:

- **The placeholder's replacement is often a struct, not a scalar.** `? sp30;` with a later
  `sp30.unk0` needs a synthesised type, so the resolver should emit a *variant* that the typedecl stage
  can fill, not a single `s32`. `m2c_placeholders.variants` bounds that enumeration; it is not wired in.
- **`(bitwise T)` is a small class.** 6 drafts, 9 occurrences (`f32` 6, `f64` 2, `s32` 1). Not worth a
  generator; recorded so it is not rediscovered.

`eval/m2c_refusal_probe.py` on the still-refused 13: `other` 11, `jump-table` 1 (`__osDevMgrMain`),
`directive` 1 (`requestMusicSequenceBank`).

## What this round actually changes

- **Capability: +4 byte-exact, +4 SOLVED** (299/233 → 303/237), all four object-verified, all four from
  m2c drafts with zero model calls. Three came from workspaces that had no draft at all.
- **Two counts corrected.** 66 cohort rows that could never compile are now draftable — 3 of them
  exact, 11 more scoreable — and the 122 placeholder-blocked rows have a named, tested, deterministic
  repair in front of them instead of a generic "Syntax Error".
- **One mechanism re-sized.** Header admission on its own is a scoreability tool, not a match tool:
  22% admit rate, 0 of its four highest scorers convert. It paid off only in composition with a repair
  that addressed a *different* defect class. That is the general lesson — a route advertised by its
  admit rate was worth exactly as much as the unrelated fix it was attached to.
- **One process rule confirmed at cost.** The admission taxonomy's dominant-looking bucket was not the
  blocker it appeared to be, and the only way that was found was reading one compiler's caret output
  rather than the bucket's count.

## Files

| path | what |
|---|---|
| `solver/m2c_placeholders.py` | placeholder rewrite + bounded variants |
| `tests/test_m2c_placeholders.py` | 8 tests, firing and declining |
| `eval/header_admission.py` | header + repair-chain admission route |
| `eval/repaired_admission.py` | converts admitted sources through the repair rungs |
| `eval/blank_draft_census.py` | 79 no-draft workspaces, with the marker |
| `eval/m2c_refusal_probe.py` | why m2c refused, captured instead of discarded |
| `eval/m2c_redraft.py` | regenerate the stale drafts, atomically |
| `eval/m2c_placeholder_census.py` | 122 drafts / 1,962 attempts behind a `?` |
| `eval/placeholder_resolution_sweep.py` | placeholder rewrite alone |
| `eval/placeholder_admission.py` | the composition |

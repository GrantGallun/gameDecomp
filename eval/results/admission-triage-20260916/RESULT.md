# The admission bucket is 23 functions, not 199

Triage of the never-compiled population, 2026-09-16. Script: `eval/admission_triage.py`
(deterministic, LLM-free, read-only against `kb-sbk1.sqlite`). Raw output:
`eval/results/admission-triage-20260916/triage.json`.

This follows `eval/results/admission-20260916/RESULT.md`, which established the population and
recommended exactly this triage as its items 3 and 4. Its headline was "admission, not residuals --
199 functions, more than twice the live set of 93". **That headline does not survive.**

## The correction, in three cuts

| cut | functions | what it is |
|---|---|---|
| never compiled, all translation units | 199 | the published number |
| minus library TUs (`eval.clean_set.EXCLUDE_TU`) | **78** | 121 (61%) are `ultra`/`libmus`/`libc`/`audio` |
| minus functions no model ever touched | **23** | 55 have only `zero-token-m2c-harvest-*` attempts |

**The real admission failure population is 23 functions.** For comparison, the live set (compiled,
never exact) is 63 game functions. Admission is therefore *smaller* than the residual problem, not
twice its size.

Both cuts are the project's own definitions, not new ones. `EXCLUDE_TU` is what `eval/clean_set.py`
already uses to keep library code out of evaluation; three threads have now been bitten by counting
it. And "did a model ever try" is `attempts.model`, which is `''` for every deterministic pass.

## Why the second cut is the bigger one

55 of the 78 game functions have **837 attempts and not one from a model**. Every attempt is a
`zero-token-m2c-harvest-*` variant:

| strategy | attempts |
|---|---|
| `zero-token-m2c-harvest-m2c` | 562 |
| `zero-token-m2c-harvest-globals` | 54 |
| `zero-token-m2c-harvest-iter0-globals` | 45 |
| `zero-token-m2c-harvest-do-while` | 42 |
| `zero-token-m2c-harvest-typedecl` | 31 |
| (7 more variants) | 103 |

Nine of them carry 11 attempts each whose stored source is byte-identical:

```
#include "common.h"

// file is blank because m2c failed to decompile function
```

That is 99 attempts across 9 functions, all `model=''`, all `strategy='zero-token-m2c-harvest-m2c'`,
all `iteration=0`, all within one 179-minute window on 2026-09-01. It is **one batch of a
deterministic harvest that could not produce a draft**, recorded as 99 admission failures.

Counting these as "the model cannot write C that builds" is the same category error the
`residual-diagnosis` write-up already caught in a different place: a label that describes one thing
(whether a pass produced output) being read as a claim about another (what the model can express).

## The pipeline/model split, within the 78

Of the 78, classifying the stored `source_code` -- what the pipeline actually handed the compiler:

| dominant class | functions |
|---|---|
| C present, compiler rejected it | 63 |
| NO_FUNCTION | 12 |
| REFUSAL | 3 |

**15 of 78 (19.2%) are pipeline-dominant** -- the text handed to the compiler was not C at all. The
pre-registered floor was 10%, so H1 holds: this is not negligible, and it is fixable without any
model calls.

Two mechanisms, both already explained elsewhere and both confirmed here:

- **NO_FUNCTION (12).** Nine are the m2c blank placeholder above. The rest are real extraction
  misses.
- **REFUSAL (3 functions, 82 attempts).** `pushRaceCourseSurfaceCollisionWithVelocity` refused 9
  times out of 23. `solver/llm.py` documents the fix and its measurement (9/9 → 0/9) and
  `admission-20260916` traced it to a missing `prefill`. Confirmed independently here.

Caveat on this table: the 63 "C present" are not all model-authored. `authorized-target-history-
recovery` also writes C without a model, so the honest model-attributed count is the 23 above, and
this split describes the *text*, not the author.

## `no text symbols` covers three different things

The other write-up flagged that all 54 of these were being counted as model failures. Splitting them:

| group | count | what it is |
|---|---|---|
| library | 25 | `osAiSetFrequency`, `alFxNew`, `memcpy`, `_timeToSamples`, … -- excluded targets anyway |
| no-op | 4 | `noopThreeArgs`, `noopFourArgs`, `raceSetupMenuNoop`, `drawMenuSpriteWithAlphaClipped` |
| real | 25 | `calculateRaceTimerDelta`, `allocMenuRenderScratch`, … -- the only ones worth diagnosis |

The no-op group is the `bootThreadMain` class: IDO emits nothing because the C does nothing, so the
target bytes may not be producible from C at all. **These belong in an "impossible" bucket, not an
admission bucket.** Until they are separated, every aggregate that includes them is wrong.

## Among the failures that are genuinely the model's

Compiler error kinds across the C-present functions, and the subset where *every* error on the
function is one kind:

| error kind | functions | of which only-this-kind |
|---|---|---|
| undefined identifier | 30 | **19** |
| syntax error | 24 | **12** |
| do-while ban | 5 | 1 |
| redeclaration | 2 | 1 |
| selector requires struct/union | 1 | 0 |

So the two real model-side admission problems are **19 clean undefined-identifier functions and 12
clean syntax-error functions** -- matching `admission-20260916`'s finding that syntax, not do-while,
is the largest clean bucket, and that a lowering alone would not have admitted them.

## A logging gap that limits all of this

`extract_status` is `None` or empty on **1,156 of 1,418** attempts in this population, and
`done_reason` is `None` on 1,072. The distinction this whole triage is trying to draw --
"the extractor mangled it" versus "the model wrote bad C" -- is exactly what those two columns were
supposed to record, and they are mostly blank. The classification above therefore reads the stored
text directly, which works, but it cannot tell a truncated generation from a dropped one.

## Recommended order

1. **Stop counting library TUs and non-model attempts as admission failures.** One filter, and it
   changes the headline from 199 to 23. Every downstream aggregate inherits the fix.
2. **Re-run the 23 with `prefill` set.** That is the whole real admission population, it fits in an
   hour, and the refusal fix is one line at two call sites.
3. **Move the no-op `no text symbols` functions to an impossible bucket.** Four functions, and they
   currently corrupt every rate computed over admission.
4. **Then the 19 undefined-identifier functions.** Finite, enumerable, and `project_headers`'s
   `prompt_context` is documented as covering 6 of 7 of them -- it is already wired behind
   `declarations=True` and default-off.
5. **Backfill `extract_status`** so the next triage does not have to infer authorship from text.

## What this does not change

The `basin-escape` SIGNAL, the `residual-diagnosis` NULLs, and the regalloc-vs-structural axis
finding are all unaffected -- they are computed over the live set, which this triage did not touch.
The live set itself shrinks from 94 to 63 when library TUs are removed, which is worth re-checking
before the regalloc/structural split is used to redirect a month.

## Reproduce

```bash
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 -m eval.admission_triage \
  --out eval/results/admission-triage-20260916/triage.json"
```

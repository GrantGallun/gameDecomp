# Why is each unmatched function unmatched? Information, search, or representation

Built 2026-09-16 to answer one question: **of the functions that are not matched, how many are
search problems and how many are representation problems?** That number decides whether the next
month goes into the data factory or into inverting compiler transformations.

Tools: `eval/residual_diagnosis.py` (classification + temporal backtest),
`eval/residual_mobility.py` (axis mobility). Both deterministic, LLM-free.

## Headline

**I was wrong that the wall is representation.** Two of my own predictions failed against the
data, and the corrected picture reorders the priorities:

| population | n | what it is |
|---|---|---|
| attempted, never compiled once | **199** | admission failure — the model cannot write C that builds |
| compiled, never exact | **93** | the residual problem |
| exact (score 100 reached) | 209 | matched |

The largest single failure population is **admission, not residuals** — 199 functions, more than
twice the live set. And within the live set, the blocking axis is movable in the majority of
heavily-attempted functions, which means **search, not representation.**

## Run 1 was void — recorded rather than deleted

The first version of `residual_diagnosis.py` produced a `CLEAN` class of 156 functions with 100
points of headroom. Two bugs:

1. It never filtered `compiled=1`, so attempts that failed to build entered as score 0.0 and were
   classified from an empty diff. A residual cannot be classified from a build that failed.
2. It read `functions.state` to find unmatched functions. **That column is `'matched'` for all
   2113 rows** and carries no information. "Unmatched" has to be derived from `attempts`.

Both are fixed and documented in the module docstring as REVISION 1.

## Prediction 1 failed: pass-ownership does not predict stagnation

`residual_diagnosis.py` classifies by which fault group dominates, using `signals.py`'s own groups
(`repairable` / `conditional_repair` / `no_repair_implemented` / `unrepairable`), then backtests:
classify on attempts before a cutoff, measure forward improvement after.

| class | n | improved forward | rate |
|---|---|---|---|
| REPRESENTATION | 29 | 9 | 0.310 |
| REPAIR_PENDING | 3 | 1 | 0.333 |
| MIXED | 1 | 1 | 1.000 |
| INFORMATION | 1 | 0 | 0.000 |

**NULL** — labels do not separate (H1 required ≥10 points and p<0.05; the observed max-min was
driven by n=1 cells). And the direction is informative in itself: REPRESENTATION functions improved
forward at 31%, no worse than REPAIR_PENDING at 33%.

The reason is a category error I made when mapping these onto information/search/representation:

> `signals.py`'s `no_repair_implemented` and `unrepairable` describe what the **pass library**
> owns. They say nothing about what the **search** can express. The model can write any C, so "no
> pass repairs this" is not "this is not representable."

So the NULL is the correct result *for that question*. Ownership is a map of deterministic repair
coverage, not of reachability. It is still useful as a coverage map (see below), but it cannot
answer the question that was asked.

## Prediction 2 failed: the axis is reachable more often than not

The right question is narrower and directly measurable: **for the fault class that dominates a
function's residual, has any attempt ever got below where the first attempt started on it?**

| attempts | funcs | below first attempt | below best attempt | P(never) |
|---|---|---|---|---|
| [1, 3) | 14 | 0 | 0 | 1.000 |
| [3, 6) | 8 | 0 | 2 | 1.000 |
| [6, 15) | 19 | 3 | 2 | 0.842 |
| **[15, ∞)** | **52** | **31** | **33** | **0.404** |

With ≥15 attempts, **60% have moved their blocking axis at some point.** The axis is reachable;
these are search failures. Only 40% never moved it, and that residue is the representation
candidate set.

Two definition notes, since the answer depends on them:

- The first run measured `floor < count at the BEST attempt`, which **reports "never moved"
  whenever the best-scoring attempt is the one that already minimised the axis** — the exact case
  where the search moved it furthest. Corrected to the first-attempt baseline; both columns are
  printed above so the sensitivity is visible.
- `bootThreadMain` illustrates a blind spot the metric cannot cover. It is known to be unreachable
  (the final instruction is compiler padding, not code), yet it is *not* flagged here, because its
  structural count did drop across attempts. **An impossible function and a search-limit function
  look identical to this measure.** There is a fourth category — impossible — that mobility cannot
  separate.

## The hard core: 37 functions that never moved

37 functions with ≥6 attempts never got below their first attempt on the blocking axis.
By axis: **regalloc 32, structural 4, ordering 1.**

That is the opposite of the project's stated expectation. `CLAUDE.md` names branch shape, missing
instructions and jump tables as the medium/large/huge residual — and that may still be true *for
those size classes*. But across the live set as a whole, the dominant axis is regalloc (57 of 93)
vs structural (30), and in the never-moved core it is regalloc 32 vs structural 4.

The worst offenders are heavily-attempted, which is what makes them representation candidates
rather than under-explored ones:

| function | attempts | best | axis | first | floor |
|---|---|---|---|---|---|
| updateRacePl… | 1715 | 96.15 | regalloc | 1 | 1 |
| updateRacePl… | 951 | 99.71 | ordering | 0 | 0 |
| serviceRumbl… | 429 | 95.49 | regalloc | 27 | 27 |
| __osBlockSum | 50 | 81.49 | regalloc | 29 | 29 |
| __cosf | 47 | 54.48 | regalloc | 37 | 37 |
| findRaceItem… | 45 | 77.54 | regalloc | 50 | 50 |
| updateEnding… | 24 | 99.74 | regalloc | 2 | 2 |

Caveats on this list: some entries are libultra (`__osBlockSum`, `__cosf`, `__osPfsSelec`,
`__osPopThrea`), which `eval/clean_set.EXCLUDE_TU` excludes from targets — the working thread hit
the same contamination when its learnability objective picked `__cosf`/`__sinf`. Those should be
filtered before the list is used. And "never moved in 6 attempts" is not evidence; only the
high-attempt entries are.

## Coverage map (the one thing run 1 got right)

Using the ownership taxonomy as a *repair-coverage* map rather than a reachability claim:

| class | live | share | of which ≥90% score |
|---|---|---|---|
| REPRESENTATION (regalloc + structural) | 86 | 91.5% | 31 |
| REPAIR_PENDING (layout + immediate + ordering) | 5 | 5.3% | 3 |
| INFORMATION (reloc) | 1 | 1.1% | 1 |
| MIXED / CLEAN | 2 | 2.1% | 1 |

**91.5% of live functions carry a residual that no deterministic pass owns**, 31 of them above 90%
score. This independently confirms what the working thread found from the other direction ("there
are zero mid-band functions where every classified fault is owned"). The pass library covers ~7% of
what is left.

## Revised priorities

1. **Admission first — 199 functions, the largest bucket.** Nothing else matters for them until C
   builds. This is the same failure the working thread hit as a refusal regression (4 refusals, 1
   no-extract, against 0 in 2,197 historical attempts).
2. **Search second — the majority of the live set.** 60% of heavily-attempted functions can move
   their axis; they simply have not converged. The factory is aimed correctly here.
3. **Representation third — a real but small core.** ~21 of 52 heavily-attempted functions never
   moved the axis. Small enough to study individually rather than architect for.
4. **Do not architect around structural faults.** Across the live set, regalloc blocks more
   functions than structural does (57 vs 30), and it is 32 of the 37-function hard core. If the
   next month is spent inverting branch-shape transformations, it is aimed at the smaller axis.

## Reproduce

```bash
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 -m eval.residual_diagnosis"
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 -m eval.residual_mobility"
```

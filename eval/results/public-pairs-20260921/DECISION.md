# The public corpus: what it is for, and what is actually left

Written after checking both, because the answer changed twice while checking. Short version: the corpus is
real and worth keeping, it is **not** the next step, and the population it looked like it should feed is
seven functions, not two hundred.

## 1. What the corpus is

`eval/results/public-pairs-20260921/` — 2,815 (assembly, source) functions harvested from three pinned
public decompilation repositories and compiled with IDO 5.3:

| repository | variant | pairs | train | dev |
|---|---|---:|---:|---:|
| Diddy Kong Racing | pal.v80 | 905 | 890 | 15 |
| Mario Kart 64 | eu.v11 | 557 | 557 | 0 |
| Super Mario 64 | eu | 1,353 | 1,261 | 92 |
| **total** | | **2,815** | 2,708 | 107 |

Plus 27 repair exercises (25 train, 2 dev) built by mutating one integer immediate in a public leaf
function, each with a compiled exact answer. The record shape is honest: `source` is the real function,
`asm` is the disassembly of **its own compiled object** with relocation annotations, and `verification`
reads `compiler-derived; replay-certified object sections and relocations; not ROM-verified`. The README
states its own limits (artificial mutation, narrow curriculum, dev split is not a sealed evaluation) more
plainly than most human-written READMEs would.

Checked, not assumed: 2,815 records on disk, first record `audspat_init` from `src/audio_spatial.c` with a
638-char source and 2,227-char asm carrying `R_MIPS_HI16 gSpatialSoundTable`; the 27 repair tasks carry
`parent{compiled, exact:false}` / `child{exact:true}` pairs.

## 2. What I would not do with it

**Not SFT for assembly → C.** Three measured reasons, all from this project's own record:

1. It is the direction already measured as weak. The stated baseline to beat is **~1.2% byte-exact** for
   published single-shot SFT, and this project's own experiments moved *away* from generation-from-scratch:
   prompt enrichment produced seven nulls, and handing over oracle-grade types made results **worse**
   (mean best 26.5 → 11.2 against 35.3 → 31.5, difference-in-differences −11.5).
2. SM64 and MK64 source is in the models' pretraining data — `CLAUDE.md` names SM64 explicitly as the
   contamination risk. So the 107-function dev split cannot measure capability, and much of the training
   signal is what the model has already memorised.
3. The 27 repair exercises mutate **one integer immediate**. That is not the pipeline's failure
   distribution; the pipeline's drafts fail on placeholders, missing declarations, absent members and frame
   layout. The project's own rule is to train only where a positive exists — an artificially damaged parent
   is not a positive the pipeline can produce.

And none of it adds a match to the ratchet, which is the only currency.

## 3. What it is for

**(a) Idiom ore for the residual class that actually dominates.** This round's reading of the four
highest-scoring near misses: instruction delta 0, −3, +1, +1, with the differing lines classified
regalloc 9 / regalloc 77 / regalloc 6 / regalloc 81. The residual is **register allocation**, not
declarations. 2,815 functions compiled by the *same compiler* are, in effect, a labelled dictionary of
"C shape → allocation shape", and that is minable deterministically — no model, matching this project's
rule that miner passes stay LLM-free — into candidate mutations for `solver/regalloc_search`, which is the
one operator that closed a function today.

**(b) A scale bench.** The corpus has ground truth, so "does the catalog close a function it did not
write?" can be asked thousands of times instead of seventeen, with no contamination risk to the target
game because it is not the target game.

## 4. What is actually left, corrected twice

The first query said **218 near misses** in the real target's KB: functions with a compiling attempt and no
exact attempt. That number is wrong as a work list, and the way it is wrong is the exact counting defect the
audit found a scaled-up version of:

* **211 of the 218 are implemented in the reference decomp's `src/**`** — solved.
* `functions.state` cannot separate them: it reads `matched` for **all 2,113** rows, so it is an inventory
  column, not a solve indicator. A guard keyed on it excluded 218 of 218 for the wrong reason while looking
  exactly like a clean guard.
* What is left is **7 functions**: `__osPopThread` (95.000), `osEPiRawWriteIo` (91.474), `osEPiRawReadIo`
  (79.150), `__sinf` (63.297), `__cosf` (54.593), `osPiRawStartDma` (50.643), `drawMenuAsciiFontTile`
  (11.399). Two of them were already measured this round and did not close.

The KB's whole shape, for the record: **2,113 functions, 346 with an exact attempt, 218 whose best attempt
compiles (~211 solved in `src/`), and 1,549 with no compiling attempt at all.**

So the remaining mass is not the finish line. It is **the front door**: on the order of 500 attempted
functions have never produced a compiling candidate, which is where the intake sequence works, and its
measured yield on the frozen 200-state frame is 32 IDO-compiling → 19 frontend-passing → **2 exact**.

## 4b. The seven, measured — and the finish-line vein is exhausted

The whole remaining near-miss set was run through the registry catalog (13 transforms including
`regalloc-search`), from each function's own best candidate, depth 2, beam 3, 60 compiles each:
`finish-line-search.json`, 281 compiles, 194 s.

| function | kb best | after the whole catalog |
|---|---:|---:|
| `__osPopThread` | 95.000 | 95.000 |
| `osEPiRawWriteIo` | 91.474 | 91.474 |
| `osEPiRawReadIo` | 79.150 | 79.150 |
| `__sinf` | 63.297 | 63.297 |
| `__cosf` | 54.593 | 54.593 |
| `osPiRawStartDma` | 50.643 | 50.732 |
| `drawMenuAsciiFontTile` | 11.399 | 12.613 |

**Closed: 0 of 7.** So the one close this round was the last cheap one in that vein, and the honest
statement is: the finish line is not where the remaining matches are. Two states moved by 0.09 and 1.21
similarity points — noise-level progress — and five did not move at all.

## 5. Decision

1. **Keep the corpus**, and use it as (a) and (b) above. It does not need to be rebuilt and nothing about it
   is blocking.
2. **Do not train on it now.** There is no useful label supply for the model's real job in it, and the
   project has already paid for that lesson.
3. **Do not plan around 218 near misses.** There are 7, and all 7 have now been measured and none closed.
4. **The next real work is the front door**, on the ~500 attempted functions that have never compiled, with
   the same discipline that produced today's close: read the residual, name the shape, then look for the
   operator. The intake sequence already converts 16% of that class; turning those conversions into matches
   is the pipeline's actual bottleneck, and the finish-line measurement above says the last step is harder
   than this morning's result suggested.

## 6. The arithmetic, stated once

| population | functions | note |
|---|---:|---|
| with an exact attempt | 346 | the KB's solved set |
| compiling, not exact, but implemented in `src/**` | 211 | solved; this is where the 218 went |
| **real near misses** | **7** | measured above: 0 closed |
| attempted, never produced a compiling candidate | ~509 | the front door |
| never attempted at all | ~1,040 | untouched inventory |

At the measured rates — 16% of front-door states reach "compiles", and the finish line closes about 1 in 17
of those — the current pipeline's remaining ceiling is on the order of tens of matches, not hundreds. Both
rates have to move for that to change, and the front door is the one with the mass behind it.

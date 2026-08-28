# Experiment queue

Ordered. One experiment per loop iteration, **one variable per iteration**.
Each entry carries its prediction *before* it runs - write the result into
`patterns/hypotheses.py`, then strike the entry and move to the next.

Standing rules for every entry:

- Never touch the held-out split (49 functions). Never put a target's own
  ground-truth source in a prompt; siblings are fine.
- Ground truth is for **checking** a mechanical pass, never for feeding one.
- **Significance floor:** tier-mean deltas under 10 and exact deltas under 3 are
  noise - report INCONCLUSIVE. A result resting on a single draw is not a result.
- Exclude refusal-text candidates from failure statistics. Refusals are
  abstention under load (`refusals-are-abstention-under-uncertainty`, CONFIRMED),
  not attempts, and counting them as failures corrupts the denominator.
- No run over ~45 min of GPU without asking first. The user needs their machine.
- If a run is in flight, WAIT. Do not edit `solver/` mid-run.

---

## ~~0. Settle today's inconclusive result before building on it~~ DONE - KILLED

> Ran at n=18/arm. Refusals: A raw 4/18, C lexicon+stripped 5/18  --  gap
> **-1**, so the pre-registered kill condition fired. Compression is
> dead as a refusal fix. `strip_asm`/`lexicon()` stay in the tree
> (correct, lossless, validated) but do NOT become the default path.
> The n=6 result was noise, which is why this entry existed.


`compressing-composer-input-reduces-refusals` is INCONCLUSIVE: the direction was
right but the 20.0 rests on **one draw** at n=6.

**Change:** nothing. Raise n only - arm A (raw asm) vs arm C (lexicon +
stripped asm), ~10 functions - --  4 draws, spanning medium/large/huge.

**Prediction:** arm C's refusal rate is lower than arm A's, and arm C produces
more compiling candidates. I do **not** predict exact matches.

**Kill condition:** if arm C's refusal rate is within 1 draw of arm A's at
n=40, the compression story is dead as a refusal fix and only the free
size reduction is kept.

Why first: everything below builds on the lexicon. Do not stack four ideas on
an unconfirmed foundation.

---

## ~~1. `loop-aware-slicing-with-markers`~~ DONE - IMPLEMENTED

> Prevalence: 7 of 17 loops (41%) were split across region boundaries,
> but in only 4 of 39 functions. Implemented and verified: split loops
> 7 -> 0, change confined to those 4 functions, 35 byte-identical.
> A correctness fix, NOT a measured improvement - 10% of functions
> cannot move an aggregate past the floor. Its value is as a
> prerequisite for entry 2: a split loop is fatal once regions emit C.
> Nested-loop markers dropped; merging spans already prevents splits.


Source: WaDec Algorithm 1 (ICSE'25).

**Change:** replace branch/label cutting in `shifts.split_regions` with loop-aware
cutting - each slice holds at most one loop plus conditionals; nested loops are
replaced by a marker that doubles as the reassembly point.

**Prediction:** region summaries stay at 0 refusals (already 18/18) and become
more accurate - measurable as fewer summaries that mention control flow the
region does not contain. Check summaries against ground-truth source structure;
this is a **checking** use, so it must not feed any prompt.

Runs before #2 because better slices make sequential composition a fair test.

---

## 2. `sequential-slice-composition` - the architectural question

Source: WaDec "temporal context". The biggest gap between our design and a
published one that works.

**Change:** slices emit **C, not prose**, and each slice is given the
declarations produced by earlier slices. Replaces the one-shot composer.

**Prediction:** refusals drop sharply (no instance ever holds the whole
function). Compiling-candidate rate rises.

**Stated risk, which is the point of the test:** register allocation is global
and our measured prefix-exact depth is **0**, so sequentially emitted C may
still not compose into a byte-exact whole. A null result here is a real finding
and must be recorded as one, not explained away.

---

> **Contingent after entry 0.** Both remaining entries tune the
> lexicon, and the lexicon is no longer on the default composer path.
> Do not run either unless entry 2 puts a lexicon back in the loop.

## 3. `callee-signatures-in-lexicon`

Source: WaDec "spatial context".

**Change:** add callee signatures to `lexicon()` - they are already in the KB.
Mechanical, deterministic, evidence-tier.

**Prediction:** fewer wrong-arity call sequences. Small effect; likely below the
significance floor on its own, so measure it as a **mechanical** check (does the
emitted call arity match the target's?) rather than as a score delta.

---

## 4. `lexicon-position-at-end-of-prompt`

Source: lost-in-the-middle (Liu et al., TACL 2024); Chroma Context Rot.

**Change:** move the lexicon block to the end of the prompt, adjacent to the
instruction. A string reorder, no new computation.

**Prediction:** small positive. The lexicon currently sits between the summaries
and the assembly - the exact position the literature says loses 30%+.

Runs last: it tunes whichever architecture wins above, and re-testing it after
an architecture change would be required anyway.

---

## Not to be retried

`token-level-prompt-compression` (LLMLingua) is REFUTED categorically, not
empirically: it is lossy by construction, and under a byte-exact criterion a
dropped immediate is a wrong constant.

Prompt enrichment is REFUTED by six null results. Do not propose it again.

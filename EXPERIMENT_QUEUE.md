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

## ~~2. `sequential-slice-composition`~~ DONE - REFUTED

> Mechanism works, outcome does not. Refusals 22% -> 0% at slice and
> final level, compile rate 22% -> 42%. But exact stayed 0, mean score
> FELL 39.0 -> 21.0, and it costs ~17x more per draw (198s vs 11.5s).
> At equal compute the one-shot composer gets ~200 draws to its 12.
> Real finding: we removed 100% of refusals and gained ZERO matches,
> re-confirming that refusals were never on the critical path.


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

## ~~T1. Tell the model the SDK types it already has~~ DONE - KILLED

> arm A (current) mean 42.6, compiled 13/36. arm B (SDK types named)
> mean 36.4, compiled 10/36. Delta mean -6.2, exact +0. Kill condition
> fired. Only 4 of 36 arm-B candidates used Gfx, so the instruction was
> largely ignored -- the pre-registered Gfx counter is the only reason
> that is distinguishable from a wrong diagnosis.
>
> The diagnosis stands; the PROMPT FIX is dead. Knowing 'Gfx exists' does
> not tell the model that the symbol at 0x80124830 IS a Gfx*. That is a
> per-symbol fact and only the inference tier can supply it.
> Seventh null from a prompt-level change. Prompt work is closed.


**Why this preempts S1/S2:** reading an actual failing candidate found two
CONFIRMED root causes, and this is the first hypothesis all week whose
mechanism predicts the tier data.

- The inference tier is EMPTY: 72,845 evidence rows, 0 inference rows. The KB
  hands the model "global:0x80124830+0x0  4 byte int, signed" -- an address and
  a width. That address holds a Gfx pointer.
- The prompt STATES SOMETHING FALSE: that common.h defines only u8..f64, and
  that all other types must be supplied INLINE. common.h includes <PR/mbi.h>,
  so Gfx/Vtx/Mtx already exist -- and defining them inline is a redefinition
  error. The model is instructed into a dead end.

The 52.4% candidate for drawMenuSolidRect invented `u32 *p` with `(u8*)p + 8`
and wrote `*p = 0; p[0] = 0xE7000000;` believing those were different words.
They are the same location. It repeated that three times.

**Change:** correct the type paragraph, naming only types VERIFIED to compile
(Gfx, Gwords, Vtx, Vtx_t, Mtx, Vp, Vp_t, Light, Ambient, Lights1, LookAt,
Hilite, TexRect). OSTask/OSMesgQueue/OSThread were probed and are NOT
available, so they are deliberately not named -- naming a missing type would
turn workarounds into compile errors.

**Prediction:** mean score on the 18 failing mediums rises by more than 10,
and/or new exact matches appear.

**Kill condition:** mean delta <10 AND exact delta <3. Then type vocabulary is
not the fix, even though the diagnosis stands.

**Guard against a false null:** count candidates that actually use Gfx. If arm
B does not change what the model writes, a null means the instruction was
ignored -- a different failure from the hypothesis being wrong.

**Honesty note:** this is prompt work, and prompt enrichment is REFUTED by six
null results. The distinction claimed here is that those added facts to an
already-correct prompt, while this corrects a FALSE statement and unlocks C the
model previously could not express. If it nulls, that distinction was wrong and
must be recorded as such.

---

## ~~F1. Harden extract_c and account for truncation~~ DONE - FIXED

> Validated against the REAL stored failures: 132 of 132 handled, 0 still
> bad. Fence leak 53/53 cleaned, echoed assembly 17 refused + 15 cleaned,
> truncated literal 47/47 cleaned. 6 unit tests including a false-positive
> guard on the assembly detector. extract_c now returns "" for a
> non-answer so callers can log extraction failures.


**Why it preempts the speedups:** log mining found 132 of 956 compile failures
(13.8%) are OUR faults, not the model's -- and they are silent.

- 53 sources begin with a literal ```c. FENCE_RE needs a CLOSING fence;
  truncated output has none, the regex fails, and the fallback
  `return text.strip()` hands the compiler the opening fence.
- 47 are cut mid string or comment. Same root cause: truncation. Together,
  truncation is >10% of all compile failures.
- 32 stored "sources" are literally MIPS assembly. FENCE_RE accepts a bare ```
  block, so when the model echoes the target and no fence holds a function
  definition, `max(candidates, key=len)` picks the assembly -- the assembly is
  always the longest block.

**Change:** extract_c must never return a non-answer. Reject assembly-shaped
candidates, recover text after an unterminated opening fence, strip stray
fences, and return "" when there is genuinely no C. Callers then record an
extraction failure instead of scoring garbage as a model error.

**Prediction:** unit tests reproduce all three shapes and pass; on the next
sampled run the backtick and dollar clusters go to zero.

**Kill condition:** if a fresh run still shows backtick/dollar/unterminated
sources, the fix did not address the real path and must be re-diagnosed rather
than patched again.

**Note:** this is a CORRECTNESS fix with a deterministic test, not a score
hypothesis. It does not need GPU time to verify.

---

## P1. Permuter on the >=95% band  [INSERTED AHEAD OF S1]

**Why now:** nine functions sit at >=95%, four of them at 99.3-99.8%. At 99.8%
the candidate is one or two instructions from exact -- the register-allocation
case the permuter exists for, and the band the pipeline is supposed to route
there. run_permuter previously fabricated EXACTs by parsing scores out of
directory names; that was fixed and the fix has NEVER been exercised on real
near-misses.

It is also CPU-only, so it does not compete with anything on the GPU.

**Prediction:** at least one function closes to byte-exact. These are the
closest candidates the project has ever produced and nothing has been spent on
them.

**Kill condition:** if zero of nine close after 300s each, either the stored
"best" scores are stale (they predate the false-EXACT fix) or the permuter path
is still broken. Re-verify the seeds before blaming the search.

**Guard:** every permuter output is re-scored through the oracle. A directory
name is a claim; workspace.score is the verdict.

---

## S1. Kill the reasoning trace on slice calls

**Why now:** measured today -- ~2,500-4,000 generated tokens per slice for
~1,700 characters of answer. The trace is most of the cost, on a task that is
mechanical translation. It is also what caused the entry-2 truncation bug.

**Change:** `think=false` (not "low") on slice calls only. One variable.

**Prediction:** wall-clock per slice drops at least 40%, and fenced-block
compliance does NOT get worse -- the trace is not doing load-bearing work on a
translation task.

**PRE-REGISTERED DOUBT, recorded before running:** while verifying seeds, calls
sent with `think=False` still came back with an empty `response` and reasoning
prose in the `thinking` field ("The user wants: ..."). That suggests gpt-oss may
not honour `think=False` at all, in which case S1 saves nothing and the honest
result is "the lever does not exist on this model". If so, the fallback is a
non-reasoning model for slices, which is entry S4 territory, not a rescue of
S1.

**Kill condition:** if empty-or-truncated slice rate rises at all, or mean score
falls by more than the floor, thinking stays on. Speed is worthless if it costs
correctness.

**Measure:** tokens generated and wall-clock per slice, plus the empty/truncated
counters, on the same 6 functions. Compare against the entry-2 rerun.

---

## S2. Cache generations -- with the sampling hazard designed out

**Why now:** identical region summaries were generated THREE times today, twice
only because a harness bug forced a re-run. The Oracle already caches on
(source hash, flags hash); generation should too.

**THE HAZARD, and it is serious.** Caching on prompt hash alone would silently
destroy statistical validity: re-running an experiment to add draws would return
the SAME draws, and n would look like 2n while carrying the information of n.
That is a fabricated-confidence bug of exactly the kind this project keeps
catching in itself, and it would be invisible in the output.

**Change:** key the cache on (model, prompt hash, temperature, num_predict,
**explicit per-draw seed**). Draw i passes seed i. Re-running the same
experiment with the same seeds is free; asking for a NEW draw uses a new seed
and always generates. A cache hit must be impossible for a draw that has not
been drawn before.

**Prediction:** a byte-identical re-run of the entry-2 rerun completes in under
10% of its original wall time, and returns byte-identical candidate text.

**Kill condition:** if a cache hit is ever served for an unseen seed, the cache
is removed, not patched. Also required: a test asserting that two draws with
different seeds never collide.

**Seed behaviour: VERIFIED (2026-08-28), so the design is sound.** Probed with
a high-entropy prompt at temperature 1.3: same seed returns byte-identical text
across calls, different seeds return different text. Two earlier probes were
VACUOUS and rejected -- one compared two empty strings ("reproducible" was
trivially true), the other used a prompt whose answer was always "Blue". A
determinism check on a deterministic prompt establishes nothing.

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

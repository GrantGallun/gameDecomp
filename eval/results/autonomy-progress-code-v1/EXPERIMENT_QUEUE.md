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

## Current program: efficiency before another wave (2026-08-31)

The dependency policy and verified memory are now documented in
`WAVEFRONT.md` and `FLYWHEEL.md`. The 2026-08-31 pass completed in this order:

1. **S1 KILLED:** determine whether `think=false` actually reduces slice-call tokens.
2. **S2 PASSED:** add the explicit-seed generation cache only if its statistical
   guardrails pass.
3. **WF1 NO SURFACE:** run one mismatch-only repair on each target reserved by
   `eval/results/wavefront_token_budget_plan.json`.

WF1 is not another callee-context prompt A/B. Before any model call it must
validate the best stored parent candidate against exact-leaf callsites. A
repair call receives only the violated constraints, the candidate, and the
ordinary compile context, with `num_predict <= 1200`. If the stored candidate
already passes, its reservation is not spent.

The current cost audit is the stop sign: 36 callsite-prompt draws consumed
33,614 recorded generation tokens and produced zero exact matches. No new
factorial or blanket-context run is authorized by that evidence. The frozen
WF1 cap is 2,400 generated tokens total across two parents.

**Results:** S1's cheap capability gate fired the pre-registered kill. On the
same fenced-format probe, `think=low` returned a direct answer in 56 tokens and
0.602s; `think=false` exhausted 512 tokens in 4.585s, left the response field
empty, and surfaced only through `thinking`. The six-function GPU run was
therefore skipped. S2's live mechanical check passed: same-seed replay was a
byte-identical cache hit in 0.0063s with zero charged tokens; a new seed used a
different key and made a real generation. WF1 replayed every eligible stored
candidate: four candidates compiled and already satisfied exact-leaf argument
and return-flow facts, while one had no replayable stored candidate. No repair
activated, zero of 2,400 reserved tokens were spent, and there was no exact
promotion. Future frontier manifests must scan candidate activation before
reserving model tokens.

## AR1. Minimal residual-driven agent loop — MECHANICALLY COMPLETE

`solver/modelrepair.py`, `solver/residual.py`, and `eval/agentrepair.py` now
implement the small loop: fresh compile, exactness-first residual, one bounded
hypothesis/patch, verified child, and a diverse beam with a global model-call
cap. The model backend is loaded through a provider adapter. Only verifier
`exact=true` terminates; weighted score and raw `.text` distance are diagnostic.

One-call DEV smoke on `updateRacePlayerMode53AerialTrick` created a complete
root/proposal/child lineage. GPT-OSS proposed a compiling layout edit that
regressed 94.634 to 94.328, so the kernel retained the parent. This confirms the
mechanics, not repair efficacy. Receipt:
`eval/results/agentrepair-updateRacePlayerMode53AerialTrick-smoke.json`.

Before an efficacy run, generate roots on `sbk1_v4_clean` DEV and select by true
residual: equal `.text` length, low structural faults, and small positional byte
distance. Compare a strong provider and GPT-OSS under identical call/compile
budgets. Do not run the 13-function held-out split until that comparison and its
decision rule are preregistered; `eval.clean_set audit` must still report clean.

## AR2. Bounded tool-using GPT-OSS — ACTIVATED, NO GAIN ON ONE DEV PARENT

`solver/toolagent.py` and `eval/toolagent_ab.py` now support a paired comparison
between proposal-only GPT-OSS and a bounded observation/action controller. The
tool arm can inspect definitions, headers, focused diffs, and candidate history;
select an earlier candidate; and submit a bounded patch. The orchestrator owns
all reads outside the allowlist, compilation, rollback, lineage, and budgets.

The final activation smoke froze `randomNextObject` at attempt 19565 and gave
both arms the same six call seeds and caps. The fresh parent was a genuine local
residual: score 98.75, equal `.text` length, positional byte distance 3, two
register-allocation faults, and `exact=false`.

- Proposal-only made five scored child attempts. None improved or matched, so
  its best remained the root; it charged 3,906 generated tokens.
- Tool-using GPT-OSS executed three inspection actions and compiled one patch.
  The child regressed to score 95.625, byte distance 6, and five
  register-allocation faults, so the controller correctly retained the root;
  it charged 127 generated tokens.
- Both arms ended `exact=false`; exact delta and best-score delta were zero.
  The clean-set audit still reports no attempted or result-artifact held-out
  functions.

Earlier v1-v3 receipts were adapter-activation checks that exposed missing JSON
prefill, an action alias, and duplicate inspection handling. The v4 receipt is
the first interpretable comparison:
`eval/results/toolagent-ab-randomNextObject-smoke-v4.json`. This n=1 result is
mechanical only. Freeze the adapter and run a preregistered 5-8-parent DEV panel
before claiming that tools help or hurt repair efficacy.

## AR3. Open-book GPT-OSS — FREEDOM OFFERED, SEARCH NOT USED

The `--open-book` arm removes inspection ordering and duplicate-action guards,
accepts arbitrary requested line ranges (observations remain context-sized),
allows broad project search/read and full-source replacement, and redacts only
the selected function's reference C definition. Arbitrary host shell access is
not part of the decompilation hypothesis; the controller still owns writes and
compilation and enforces finite budgets.

The first prompt inherited bounded wording and is an activation failure, not a
quality result (`openbook-v1`). After replacing it with a dedicated autonomous
prompt, `randomNextObject` received 10 available model calls. GPT-OSS used two:
one compiling patch regressed the root from score 98.75, byte distance 3, and
two register faults to 95.625, distance 6, and five register faults; it then
finished, claiming the remaining allocation mismatch could not be changed from
source. It executed zero search/read actions. The controller retained the root.

The proposal-only control also ended at the root after ten calls and six
compiling children. Both remained `exact=false`. The clean split still reports
13 untouched held-out functions and no result-artifact contamination. Receipt:
`eval/results/toolagent-ab-randomNextObject-openbook-v2.json`.

Interpret this narrowly: unrestricted relevant tools did not cause this model
and prompt to conduct richer search on one DEV parent. It does not show that
useful retrieved evidence is ineffective. The next materially different test
is a search-budget or curiosity policy that prevents unsupported early finish,
not another expansion of the available tool list.

## AR4. Curiosity and retrieved principles — ACTIVATION CONFIRMED, EFFICACY INCONCLUSIVE

`eval/sets/principle_openbook_dev_v1.json` froze eight non-held-out compiled
parents after a zero-model census of 60 stored candidates. All eight have equal
instruction and `.text` lengths, zero instruction-count delta, at most two
structural faults, and positional byte distance 3-23. Four mechanically
activate catalog guidance. The run kept two one-variable comparisons:

1. Free open-book versus curiosity changes only the minimum finish policy.
2. Curiosity versus principled changes only retrieved compiler guidance and is
   evaluated on the four activated functions.

The first v1 run was an activation failure: the prompt demonstrated the literal
invalid value `target|workbench`, and the schema rejected the model's reasonable
`register-allocation` repair label. It is not quality evidence. After accepting
broad diagnostic labels and a real `both` search root, v2 completed all eight
functions in 1,658.9 seconds:

| Arm | N | Calls | Tool actions | Source experiments | Exact | Byte improvements | Charged tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| Free open-book | 8 | 16 | 4 | 1 | 0 | 0 | 829 |
| Curiosity | 8 | 47 | 19 | 5 | 0 | 1 | 2,426 |
| Principled | 4 applicable | 24 | 8 | 4 | 0 | 0 | 1,283 |

On the four principle-applicable parents, the paired curiosity baseline used 23
calls, seven tool actions, three source experiments, and 1,634 charged tokens;
it also had zero exacts and zero byte improvements. Thus principle guidance
added one source experiment and used fewer tokens, but did not improve a best
candidate. Curiosity reduced `Fwobble` from five differing bytes to three
(score 98.889 to 99.444) without closing it. No other best byte residual moved.

This confirms that a stopping policy can make GPT-OSS investigate and compile
more candidates. It does not establish better byte-exact performance: exact
delta is zero and the lone non-exact improvement is below the project floor.
Retrieved principles are inconclusive at n=4 and currently show no quality
signal. Broad recursive search also dominated wall time, so index/cache search
before any replication. Receipt: `eval/results/principle-agent-ab-v2.json`.

## AR5. Mechanical isolated-register-web variants — REFUTED ON THIS COHORT

The experimental register-web principle was compiled into deterministic source
operators before replay: C89 declaration permutations and initializer splits,
`register` qualifier changes, direct/pointer value webs, increment spellings,
fused preincrement lookup, and explicit global-pointer lifetimes. The frozen
panel contained three applicable DEV roots:

| Function | Root bytes | Variants compiled | Best bytes | Exact |
|---|---:|---:|---:|---:|
| `randomNextObject` | 3 | 14 | 3 | 0 |
| `reserveSoundEffectQueueReadIndex` | 4 | 3 | 4 | 0 |
| `updateRaceGameplayFlow` | 19 | 32 | 19 | 0 |

V1 exposed four invalid alias-elimination candidates and is retained only as a
debugging receipt. V2 fixed that generator error: 49/49 variants compiled in
12.5 seconds, with zero exact closures and zero byte-distance improvements.
The database contains three fresh roots plus 49 children; all 52 attempts have
parents and edges, and none overlaps the frozen held-out names.

Decision: do not promote these operators into the solver or the catalog's
confirmed set. The next useful test must search a materially different source
graph, not add more declaration permutations to this family. Receipt:
`eval/results/principle-register-web-v2.json`.

## MR1. Frozen-parent structured repair vs full rewrite — READY FOR SMOKE

This tests the local-model **repair adapter**, not the parent hypothesis that
local models can contribute useful C. A null result rejects structured
child-following repair under this budget; it does not reject independent
sampling, deterministic repair, or future trained repair models.

**Frozen population:** DEV only, six parents from each operational bucket:
non-compiling, compiled 80–95, and compiled 95–100. Functions with a positive
exact receipt are excluded. The manifest stores source and hashes so later KB
writes cannot move the population. Every parent is recompiled before either
arm; a now-exact parent is excluded from both arms.

**One changed variable:** output/action protocol.

- Control: four independent complete-file revisions, all anchored on the same
  frozen parent.
- Treatment: structured substring edits with draws=2, depth=2, beam=1. A valid
  child may become the depth-two parent; invalid depth-one proposals do not
  reduce the four-call cap.

Both arms receive the same target, parent, residual, deterministic diagnosis,
model, temperature, numeric seed schedule, `num_predict`, and four-call cap.
Generation caching makes replay mechanical. Arm order alternates by function.

**Prediction:** treatment produces at least three more exact matches than the
control across the 18 parents. Also report compilation conversions, mean score
delta, charged tokens, and exact matches per million charged tokens.

**Decision:** treatment-control exact >=3 passes the project significance
floor. A delta from -2 through +2 is INCONCLUSIVE. Control ahead by >=3 rejects
this adapter/budget. Do not promote the treatment by average-score movement
alone. A three-function `--smoke` checks only activation, lineage, accounting,
and runtime; its receipt must say `mechanical-only` regardless of outcomes.

**Runtime guard:** stop before beginning another function once 45 minutes have
elapsed. Do not interpret a partial or parent-excluded run.

**Smoke result (2026-09-01): PASSED MECHANICALLY; NOT AN EFFICACY RESULT.**
The balanced three-parent run completed in 1,288 seconds with no exclusions.
Each arm attempted 12/12 paired calls. Both produced 0 exact matches, 0 compile
conversions, and 0 best-score movement, so the required verdict is
`mechanical-only`. Structured repair charged 4,972 generation tokens versus
12,523 for full rewrites (60.3% fewer), but only 5/12 structured responses
became evaluated source children and 4 compiled; the full-rewrite arm produced
8 unique evaluated children and 5 compiled.

The seven rejected structured responses were audited from raw receipts. The
parser was correct: proposals requested forbidden `<stdint.h>`, used an empty
`old` anchor, exceeded the four-edit cap, or supplied no-op edits. The smoke
also exposed comment-only changes being treated as source edits; these are now
rejected before compilation and covered by a unit test.

The prevalence gate also fired before the full run: after excluding functions
with positive exact receipts, the DEV pool contains 40 compile-failure parents,
15 parents in 80–95, and only 5 in 95–100. Enforcing unique functions after
the other buckets leaves 2 eligible near-miss parents, so the preregistered
6/6/6 panel is impossible. The harness refuses the underfilled efficacy run.
Choose and preregister a backfill policy before spending more GPU; do not infer
quality from the smoke.

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

## ~~P1. Permuter on the >=95% band~~ DONE - 4 MATCHES RECOVERED

> Prediction held, but not by the predicted mechanism. Two functions
> 'closed' in 1-3s, too fast for search. Checking rather than claiming
> found the real story: sweeping all 78 permuter output files through
> the oracle recovered FOUR byte-exact solutions already on disk.
>
> Cause: directories are named output-0-1, where the number is a dist.py
> COST and 0 means PERFECT. The old code read it as a SCORE where 0 is
> worst -- so the permuter's best output was discarded as its worst.
> Same root cause as the false-EXACT bug, opposite direction, missed
> because that fix never asked what the false NEGATIVES looked like.
>
> Match count 29 -> 33.


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

## ~~T2. Synthesise structs from evidence~~ DONE - INCONCLUSIVE

> 0 closed, 0 improved -- but only ONE genuine test happened. The harness
> could not tell 'rewrite applied and did not help' from 'rewrite never
> applied'. Diagnosis: 1 real null, 3 where struct_names() returned
> ['break'] and nothing was rewritten, 6 with no candidate on disk.
>
> Correction: the four stuck diffs are NOT one bug. Only SlideIn is
> confirmed struct layout. RespawnSurfaceValid is a wrong BASE REGISTER;
> ExitUntilPhase3C is partly a load-ORDER swap no struct can fix. I
> generalised from one case on a superficial shared shape.
>
> structgen is kept and tested (9 tests). It is correct and validated
> against the layout that produced the byte-exact match.


**Why it preempts everything else:** proven by construction.
updateTimeTrialRecordDeltaPopupSlideIn went 99.61 -> 100.00 BYTE-EXACT by
changing only struct padding, every other line identical.

All four functions stuck at 99%+ have the same bug: wrong struct field offsets.
The permuter closed zero of five in 500s each and produced no output at all for
three, because struct layout is not in its mutation space -- so the router's
">=95% means register allocation" rule misclassifies this whole band.

The KB already states the correct offsets. The model uses them as field NAMES
and then lays the fields out at 0, 4, 8. It reads the facts and ignores them --
the same failure as T1, where only 4 of 36 candidates used Gfx after being told
it existed.

**Change:** generate the struct mechanically from the evidence tier and rewrite
the candidate's definition. Deterministic, LLM-free, no GPU. Seven prompt-level
nulls say restating facts does not work; this enforces them instead.

**Prediction:** at least one more function closes to byte-exact. Three are known
to have this exact bug.

**Kill condition:** zero closed AND no score movement means offsets are not the
whole story on those functions -- re-diagnose rather than iterate.

**Note:** unobserved bytes become explicit padding, never closed-up fields.
Closing gaps is precisely the bug being fixed, and an unobserved byte is
unknown, not absent -- CLAUDE.md invariant 5.

---

## ~~T2b. Re-run struct repair with names preserved~~ DONE - NO SURFACE

> 0 rewrites applied, 0 closed -- not because the technique fails (it has
> closed two functions by hand) but because the band has nothing to work
> on: of 12 functions, 5 have no candidate on disk, 3 have candidates
> with no struct, 1 has no param evidence, and the one testable function
> already has correct padding.
>
> Decisive finding: regenerating a struct from ONE function's evidence
> DELETES every field that function does not touch, breaking the body.
> A struct is a PROGRAM-WIDE fact. That is a first-party argument for the
> inference tier, independent of any external benchmark.
>
> structgen.repad() is the safe form and reproduces the SlideOut fix.


**Why:** T2 was inconclusive because of two harness bugs, not because the idea
failed. structgen renamed field28 to field_28, breaking every function body,
and the resulting COMPILE FAILURE was reported as "no improvement". Separately
struct_names() matched "} break;" so structless functions looked like tested
ones. Both fixed.

**Mechanism now proven twice, by padding alone:**
SlideIn 99.61 -> 100.00, SlideOut 99.999 -> 100.00.

**Change:** none to the idea. Re-run with names preserved and a harness that
counts applied / broke / improved / closed separately.

**Prediction:** at least one more function closes byte-exact.

**Kill condition:** rewrites apply, compile cleanly, and nothing closes or
improves -> padding is not the remaining issue there; re-diagnose rather than
iterate.

---

## ~~S1. Kill the reasoning trace on slice calls~~ DONE - LEVER ABSENT

> The capability gate stopped the run. `think=false` was slower and consumed
> the whole 512-token probe cap because this local gpt-oss/Ollama combination
> returned reasoning through `thinking` instead of a direct response. This is
> the pre-registered "lever does not exist" outcome, not evidence about whether
> reasoning is useful. Receipt: `eval/results/s1_slice_think_probe.json`.

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

## ~~S2. Cache generations -- with the sampling hazard designed out~~ DONE - PASSED

> `solver.llm.generate` now caches only explicitly seeded calls. The key covers
> the model, prompt/prefill hashes, reasoning mode, endpoint, namespace, and all
> runtime options. Live check: same seed hit with byte-identical text and zero
> charged tokens; a new seed missed under a distinct key. Receipt:
> `eval/results/s2_generation_cache_pilot.json`.

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

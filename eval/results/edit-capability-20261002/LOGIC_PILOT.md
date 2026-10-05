# Logic pilot: does training on compiler-logic tasks help, and which kind? (preregistered 2026-10-03)

Written before any arm is trained or scored. Proposed by the external audit
(`docs/model-capability-training-audit-20261003.md`, "My proposed next experiment", item 5) and authorised by the user.

## Data
`~/decomp/experiments/edit-capability-20261002/logic-pilot-v3/logic-v3/tasks.jsonl` (eval/logic_tasks.py over
`public/context-v3.jsonl`): planted pairs inside SM64 / MK64 / DKR translation units, every prompt carrying a context
whose standalone compile reproduced the full-file instructions; `logic_grade.py --self-check` must report 0 failures
before anything trains. Splits: MK64+DKR file groups -> train / exam; all of SM64 -> check.

## Arms (student: `~/decomp/models/qwen2.5-coder-7b`, QLoRA r=8, `eval/train_source_repair.py`)
| arm | trains on |
|---|---|
| base | nothing |
| base_fs | nothing; two fixed train examples per task kind in the prompt |
| repair | logic-explain (residual -> edit script) |
| logic | logic-predict + logic-need (incl. twins and withheld-fact tasks) |
| mixed | half of each, interleaved |

Matched budget: the same N train examples per trained arm (`logic_arms.py`, N = min(2400, |repair|, |logic|)
after tokenization removes examples longer than 3072 tokens, rounded down to a multiple of four),
one epoch, batch 1 x grad-accum 4, lr 2e-4, max_seq_len 3072, same seed. Inference: greedy, max 512 tokens, the
same frozen exam for every arm (`eval/logic_exam.py freeze`, prompts <= 9000 chars).

## Measures (logic_grade.py)
- predict: label (SAME/DIFFER) and full (label + exact multiset of changed rows), per variant (base / twin).
- need: the hidden type is explicitly one of the two compiler-tested alternatives. Correct = `NEED: <the right
  name>` if the answer differs between those alternatives, the ordinary answer if both agree.
  Reported separately for the two, so "always NEED" or "never NEED" is visible.
- explain: the edit script applied and compiled alone in context reproduces the target listing.
Primary split: exam (MK64+DKR files never trained on). Secondary: check (SM64; the base model may have seen its
source, so a gain there is not cleanly skill).

## Reading the result
Per arm and task kind, paired against base on the same items: gained / lost counts. An arm "helps" a kind when it
gains more items than it loses on exam. A logic-task gain is auxiliary: it becomes a decompiler improvement only if
it also helps real repairs, which this pilot does NOT measure (no SBK1 or natural-residual panel yet). One seed, one
budget: a pilot, not a capability claim.

## Continuation amendment (October 3, before full training or held-out scoring)

Claude's v2 development smoke exposed a blank-line parsing crash and an output-format confound. The continuation
fixes blank rows, removes the source-repair C fence from logic completions, and includes the `base_fs` format
control. The v2 smoke used train items and may overlap its few-shot demonstrations; it is plumbing evidence only.
Its 127 dataset completions passed self-check; the revised v3 set also passes all 127.

v3 bounds each missing-type question to exactly the two tested alternatives. One unchanged signedness probe
does not establish that an arbitrary unknown width is irrelevant. This changes the prompt version, so stale v2
training files are refused. The compiler context builder and its ongoing measurements need no restart.

Examples and optimizer steps are matched, not supervised tokens or wall time. `arms.json` reports each arm's token
totals; a deadline-shortened run is rejected for comparison. The character cap for inference is not a tokenizer
window guarantee. Every arm sees the same frozen items and decoding. No weighting is tuned from exam results.

`logic_pilot.py` waits on the current context builder's process-exit event, validates its completed receipt,
exports v3, runs the full compiler self-check, prepares token-filtered arms, and freezes/checks the split groups.
It runs base/base_fs, trains three adapters, serves all three for their exams, and writes paired label/row
comparisons. Every stage must succeed. Missing answers, failed requests, absent adapters, and unequal completed
budgets stop the run. Failed inference requests remain in the answer log with their error; generation receipts
are retained for successful requests. `status.json` reports the current stage or failure.

The runner owns only its own server process group; it refuses an occupied port. It neither unloads other models
nor broadly kills servers. Inference reserves 70% of GPU memory with four sequences; training uses a 70% allocator
cap, two CPU threads, and the opt-in completion-logit loss (loss/gradient equivalence tested against Qwen's full
masked loss). No sleep loop is used. Output directories cannot be reused automatically. A separate 8-example,
2-step mixed-adapter run validates training/serving plumbing and is excluded from all capability results.

The original context builder failed before its receipt despite a misleading shell completion marker. Recovery
retains the verified prefix and completes remaining function groups in new files. The pilot requires the final
recovery receipt and output digest; the original marker files are ignored. See `RESUME_20261003.md`.

## Result (2026-10-04; resumed after the train_logic CUDA crash, same code/tasks/exam verified)

Exam split (MK64+DKR files never trained on), "full" metric, N=1948 examples per trained arm:

| task (n) | base | base_fs | repair | logic | mixed |
|---|---|---|---|---|---|
| explain, compiled to target (130) | 2 | 15 | **79** | 0 | 72 |
| predict, label+rows (231) | 46 | 36 | 52 | **100** | 95 |
| predict TWIN, label+rows (180) | 2 | 1 | 3 | 7 | 6 |
| need, relevant -> NEED (93) | 0 | 0 | 0 | **54** | 18 |
| need, control -> normal answer (209) | 63 | 47 | 49 | 95 | 92 |

Lost vs base is small on the trained kinds (repair explain -1; logic predict -0, need -1). The few-shot control gains
little, so the gains are not formatting alone. Check split (SM64) shows the same pattern (repair explain 0 -> 47;
logic predict 22 -> 52; NEED 0 -> 22).

Process check FAILED: twins (the same pair under one retyped declaration) stay at 1-7/180 for every arm, and twin
LABEL accuracy falls for trained arms (base 158 -> logic 134, mixed 144). The arms learned to predict a pair's rows
from the pair, not to read the declaration that decides them. NEED on relevant facts (54/93) shows partial sensitivity
to missing information, but answering correctly when the context CHANGES is not learned. One seed, planted edits only,
no SBK1 or natural-residual panel: a pilot.

## Localization A/B (2026-10-04, `loc_ab.sh`)

The same 216 frozen explain tasks, with and without the `line_map.localize` block (direct lines, regions, declarations,
insertion points), compile-graded. One greedy pass per arm.

| arm | plain | loc | gained / lost |
|---|---|---|---|
| mixed adapter (7B, never trained with the block) | 117 | 119 | +15 / -13 |
| gpt-oss:20b (untrained) | 41 | 50 | +30 / -21 |

By class, plain -> loc. mixed: arith_op 39->43, arg_swap 12->14, drop_stmt 25->22 (of 67), k2 0->0 (of 9).
gpt-oss: const 15->19, arith_op 12->16, drop_stmt 4->3.

Reading: the block reshuffles answers a lot but nets little. It helps the value classes slightly and does nothing for
missing statements, the largest hole, though it names the insertion point. For drop_stmt, WHERE is not the
bottleneck: rebuilding WHAT (callee, arguments, value vs address) from the assembly is. Neither net change is clearly
outside noise (gpt-oss +9 on 51 discordant pairs; inference noise is about +-3). Next: compiler-labelled reading tasks
(call arguments, &-vs-value, store order) and graded intermediate claims, not more prompt context.

## Reading pilot (2026-10-04, `reading_pilot.sh`; written before results)

Diagnosis behind it: of 39 missed missing-statement repairs (multi-turn RL arm, exam + check), 16 are assignments
(70% of those fail), 8 calls have the right callee with wrong argument values, 5 the wrong callee, 6 the exact call in
the wrong place. Localization did not help (above). So: train the reading itself.

Data (`reading_tasks.py`, 2,232 context-checked functions, 42 s): for each simple statement the compiler's line table
credits with instructions, (a) **logic-read**: the line is blanked, the target listing is shown with that line's rows
marked `>`, the answer is the statement, graded by substituting it and compiling (any statement producing the same
code is right); (b) a **drop_stmt explain** task from the same deletion, from every eligible line rather than the
planter's first three. Train 2,820 read (1,602 store, 858 call, 226 assign, 134 other) + 1,523 drop; exam 106; check
(SM64) 3,155. Self-check 7,604/7,604 after fixing a grader bug it found: markdown cleanup deleted `**` from C
double-pointer casts (2 answers; the same cleanup fed reward_server's multi-turn feedback, now fixed there too).

Arms, each trained from the base with the logic pilot's mixed recipe (one epoch, completion-only loss):
`read` = mixed + 974 logic-read + 974 reading drop_stmt; `more` = mixed + 974 further logic-explain + 974 further
predict/need from the same logic-v3 export. Equal example counts, so `read` vs `more` isolates WHAT was added.

Criteria fixed now: primary = drop_stmt explain on the frozen logic-v3 exam (exam + check), `read` vs `more`,
paired. Secondary = the frozen reading exam by statement kind (store / call / assign), and all logic-v3 explain
(a gain on drop_stmt that costs other classes is reported as such). Single greedy pass, --jobs 4 (batched serving
noise about +-3 per arm).

## Regression exam: general work (2026-10-04, `decompile_tasks.py`; written before results)

Narrow pilots can cost general skill (trained arms lost twin-label accuracy; RL collapsed NEED), and none of the
targeted exams would show it. `decompile-v1`: the whole function from its signature, declarations and instructions,
on held-out functions only (51 dev + 300 sampled SM64 of 981; 336 after the prompt cap), graded by compiling:
exact, compiles, and instruction rows still different (paired, on tasks where both arms compile). Never a training
kind. Answer cap 1024 tokens. Runs through `eval/arm_runner.py` (`regression-v1/spec.json`) for base, mixed,
grpo_mt, read and more, queued after the reading pilot.

Adoption rule for any adapter from now on: it must gain on its target AND not regress here against the adapter it
would replace: exact lost <= exact gained, compiles lost <= compiles gained + 3, and `farther` not exceeding
`closer` by more than 2 x sqrt(closer + farther) (a two-sigma sign test). Otherwise the incumbent stays.

## Reading pilot + regression exam: results (2026-10-05, `regression-v1/summary.json`)

Run through `eval/arm_runner.py` (one adapter per server; continuous batching, 16 sequences, fixed 2.5 GiB KV).
Single greedy pass per arm.

**Primary (pre-registered): missing-statement repairs, `read` vs `more`.** Over both exams (logic-v3 exam + check,
and the reading exam's drop_stmt repairs; 277 tasks): read alone solves 42, more alone 18 (paired; a sign test puts
this about 3 sigma from chance). By exam: logic-v3 drop_stmt 33/67 vs 27/67; reading exam 89/210 vs 71/210 (mixed 28/67
and 61/210). **Confound (found while writing up, after the run):** `read` trained on 1,425
drop_stmt repair examples and `more` on 833, so the gain cannot yet be split between reading practice and more
examples of the same task. A class-matched control is the follow-up.

**Secondary.** Reading the blanked statement (reading exam, SM64 check): store 67 -> 92/151 (more -> read), call 89 ->
101/170, assign 12 -> 18/26. Repair classes other than drop_stmt (logic-v3, 149 tasks): mixed 89, read 86, more 96: each
arm is better at what it saw more of (more had ~2x the non-drop repair examples). Predict / NEED: read 155 / 182, mixed
154 / 179, more 163 / 209 (more had 2x those examples).

**Regression exam (decompile, 336 held-out functions; raw / after the campaign's cleanup):**

| arm | compiles raw | compiles tooled | exact |
|---|---|---|---|
| base | 0 | 35 | 0 |
| mixed | 192 | 214 | 15 |
| grpo_mt (multi-turn RL) | 200 | 222 | 17 |
| read | **229** | **237** | **21** |
| more | 184 | 213 | 17 |

Training never included whole-function decompilation, yet every trained arm writes compiling C for most functions
(the base pastes assembly into `__asm__` for 75%). Adoption rule against the incumbent (mixed):
- grpo_mt: exact +2 / -0, compiles +19 / -11, closer 36 / farther 27 -> **passes** (RL did not cost general skill).
- read: exact +10 / -4, compiles +63 / -26, closer 73 / farther 52 -> **passes, and improves general decompilation**.
- more: exact +6 / -4, compiles +36 / -44 (net -8, within the +3 tolerance? no: 44 > 36 + 3) -> **fails** the compile
  criterion.

Decision under the rule: `read` replaces `mixed` as the incumbent SFT adapter. Reading practice (writing a statement
from its instructions) transferred to writing whole functions from their instructions, the one skill no arm trained.

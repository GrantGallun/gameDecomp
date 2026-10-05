# Train the policy to act on tool observations: implementation, dataset, adapter, paired evaluation

Specification: `docs/deepseek-tool-policy-training-next.md`. Date: 2026-09-21.
Status: phases 1–6 implemented and run. The bounded correction round (spec §5, "ONE bounded
training-only correction round") is NOT run — stated under **What remains**.

Read `PANEL.md` in this directory first: the splits, the two environments and the metrics were frozen
there BEFORE any model arm was run. `BOUNDARY-FIX.md` in `tool-agent-20260920/` carries the phase-1
record and a correction that this work forced.

## 1. The interface was still broken, and the previous negative was not evidence about the model

The preceding phase-1 work fixed nine boundary defects and re-ran the head-to-head. That re-run is
invalid, and the report claiming "the negative survived the repair" is corrected in
`tool-agent-20260920/BOUNDARY-FIX.md`:

- `observation()` was taught to emit the tool result, and every renderer test passed — because the
  tests called `observation()` directly. `ModelPolicy._render`, one call earlier, still projected
  history to `action/status/changed/exact`. Replaying the old projection renders a step as
  `compile {} -> failed` with the compiler's error text absent. **The model still never saw a tool
  result**, so the "repaired" head-to-head measured the same broken prompt.
  Pinned by `tests/test_tool_boundary.py::test_the_model_policy_prompt_carries_the_tool_result`,
  which calls the policy.

Four further defects found while building the dataset, all of which discarded the observation:

| # | defect | fix |
|---|---|---|
| 10 | the loop's auto-compile kept only `exact`/`certificate_status`, dropping the fresh diff, stderr, score | the whole verdict is merged into the step it describes |
| 11 | `build_context`'s pre-episode compile was never a step, so the FIRST decision was made from a state the policy could not see — and on 11 of 12 collected functions that compile FAILS, so its stderr is the entire residual | recorded as step -1, hashed to the candidate, not charged to the budget |
| 12 | `diffrepair` returns its repair info under a nested `detail` key, which landed at `step.detail['detail']` where the renderer never looks | nested detail is flattened |
| 13 | a runner could end the episode by returning `exact: True`; nothing had certified it | only the certificate confers exactness (`certified`/`compile`); the claim is kept as `exact_claimed` |
| 14 | a runner that raised, or returned a non-dict, killed the episode | recorded as `runner-error` with type and text; the policy still gets to decide |
| 15 | cancellation was never propagated into the step record | recorded and rendered |
| 16 | `Action.needs` under-declared `compile` (no `compile_fn`) while the runner refused to run without it | declaration corrected; a test now checks the registry's declaration against every runner |

The window is now fully repaired: the policy sees the fresh candidate, the real residual, the real
tool result, the arguments, the prerequisites, the derived state and the remaining budget, through one
renderer shared by training and inference.

## 2. The compilation contract, decided

Spec §1 item 5 asked for ONE contract, stated in the prompt and implemented in the runtime. Decided:

> **The controller compiles.** It compiles the initial candidate and every source a transform
> produced, and attaches the whole verdict to the step it describes. The observation therefore states
> either `ALREADY COMPILED ... exact=...` or `UNVERIFIED`. `compile` is the action that produces a
> verdict for an unverified source; compiling a source that already carries one returns the identical
> answer and spends a slot.

`ScriptedPolicy` was changed to obey the same rule (previously it compiled first and again after every
change, which was right only while the controller did not compile transforms), and to stop when the
certificate has passed or the budget is gone. Its `regalloc-search` proposal was also gated on the
declared `needs` — it used to propose an action whose prerequisite was absent.

## 3. Dataset: 48 verified records from two separately labelled sources

`eval/tool_action_dataset.py` (labels, splits, replay), `eval/tool_action_collect.py` (real episodes),
`eval/tool_action_manifest.py` (combination + weights). Manifest:
`eval/results/tool-action-20260921/dataset-manifest.json`.

| source | records | label source | how the label is established |
|---|---|---|---|
| procedural | 12 | `fixture` | rules over observable fields only, re-derived at grading time from the record's own state |
| outcome-backed | 36 | `execution` | real episodes on TRAIN-split functions (12 functions): state snapshotted at each decision, alternative applicable actions executed from INDEPENDENT COPIES under the same ceilings |

By label confidence: 35 `certain`, 10 `acceptable-set`, 3 `unresolved`. By kind after weighting: 76
procedural, 62 outcome (138 emitted from 48 records; 72 after one target per acceptable alternative).

Traces for 13 real functions, including a `_ldexpf` case whose initial compile succeeds with a
382-character diff and eleven whose initial compile fails with 45–308 characters of compiler stderr —
the residual the policy previously never saw.

Procedural cases are PAIRED, because a dataset that cannot show a policy changing its mind for one
reason is not evidence that it reads anything:

| pair | one field differs | right action differs |
|---|---|---|
| `fresh_state` / `verified_state` | does a verdict describe the current source | `compile` / not `compile` |
| `no_effect_unchanged` / `retry_after_change` | did an intervening transform move the source | `diffrepair` forbidden / allowed |

Fixtures cover malformed runner output, a cancelled search, blocked infrastructure (where `stop` joins
the acceptable set last — abandoning the episode because one route errored is the wrong lesson),
missing prerequisites, exact certificates, exhausted budgets and justified no-progress.

Balance: taking only the first acceptable action made `redraft` 65% of the set, because most late
states have already tried the cheaper transforms. Emitting one target per acceptable alternative
(capped at 3; `stop` never added by expansion) brought it to 26%, then bounded weights equalised the
families. Reproduce with `python -m eval.tool_action_manifest ...`.

Split isolation is by FUNCTION FAMILY, never by function (`splits.json`: train 28 / dev 24 / test 8
functions). Families already reported on by the earlier tool-agent panel are pinned to dev. The
procedural exercises use a different surface per split — different function name, source text,
residual text and tool arguments, identical rules — so transfer is separable from name memorisation.

## 4. Adapter

`eval/train_tool_action_sft.py`. Completion-only cross-entropy: prompt/system/history/tool-result/pad
tokens are `-100`; the trained span is `ids("{") + ids(completion[1:]) + EOS`, built with the SAME
separate-prefill call `ModelPolicy.choose` uses. The token report confirms the reason that matters:
**138 of 138** examples have a one-call tokenization of the completion that differs from the two-call
span (a byte-level BPE fuses `{"`), so a trainer that tokenized the completion in one call would have
trained a different sequence from the one inference produces.

| | |
|---|---|
| examples | 138 (0 dropped) |
| prompt / completion tokens | mean 1545 / 14.5 (max 1785 / 16) |
| steps, epochs | 54, 3 |
| LoRA | r16, alpha 32, dropout 0.05, 40.4M trainable (0.919%) |
| loss | 1.548 → 0.067 |
| wall, peak GPU | 592 s, 9.21 GB (cap raised from the default half-card to 0.75 because the cap, not the model, was binding) |

**The loss is not evidence of anything** (spec's closing instruction). 138 examples at 0.067 means the
adapter memorised a small set; whether it learned a rule is what §5 measures.

## 5. Paired evaluation: procedure improves, game-solving does not

Identical renderer, action space, runtime guards and budgets for all three arms. Receipts:
`eval-<arm>-{procedural-test,procedural-dev,panel-dev}*.json`. Regenerate the table with
`.cache/recon/eval_tool_actions.sh` and `.cache/recon/eval_tool_actions_adapter.sh`.

### Held-out procedural exercises (mechanical labels, no compiler)

| surface | arm | acceptable | rate | premature stops | honest stops |
|---|---|---|---|---|---|
| test (held out) | A scripted | 12/12 | 1.000 | 0 | 3 |
| test (held out) | B base | 9/12 | 0.750 | 0 | 1 |
| test (held out) | C adapter | 11/12 | **0.917** | 0 | 2 |
| dev (held out) | B base | 10/12 | 0.833 | 0 | 2 |
| dev (held out) | C adapter | 12/12 | **1.000** | 0 | 3 |

Arm A scores 1.000 because it now implements the same mechanical rules: it is the PROCEDURAL CEILING
here, not a discriminating baseline. It says nothing about game-solving.

### Observation ablation, on DEV

| arm | full details | results only, state line kept | results AND state line removed |
|---|---|---|---|
| B base | — | 0.833 | 0.667 |
| C adapter | 1.000 | 1.000 | **0.833** |

Read this carefully, because the two levels answer different questions. Removing the RESULT fields
(stderr, diff, reason, score) costs the adapter nothing: it selects an acceptable action from the
derived state plus the action→status history, which is a legitimate reading of the observation.
Removing the DERIVED STATE too costs it one decision in six (and reintroduces one repeat-after-no-effect
that full detail did not have), while the base model falls from 0.833 to 0.667. So the trained policy's
procedure rests on the derived state, and when that is withheld it degrades rather than staying
perfect — the demonstrated ability is not a fixed order replayed regardless of input, but it is also
not a diff-reading ability. Claiming "it reads the residual" would be unsupported: that is what the
first ablation level shows is NOT necessary for its performance on these exercises.

### Real repair panel: 6 dev functions, budget 5, real drafts, real runners, real certificate

| arm | decisions | acceptable | rate | missing-prereq calls | redundant compiles | premature stops | certified matches | internal compiles | tokens | seconds |
|---|---|---|---|---|---|---|---|---|---|---|
| A scripted | 26 | 21 | 0.808 | 0 | 0 | 6 | 0 | 7 | 0 | 4.8 |
| B base | 30 | 18 | **0.600** | 3 | **9** | 0 | 0 | 9 | 440 | 30.9 |
| C1 adapter | 30 | 25 | **0.833** | 3 | **0** | 3 | 0 | 13 | 399 | 31.7 |
| C2 adapter + correction round | 30 | 25 | **0.833** | **0** | **0** | 6 | 0 | 9 | 406 | 30.4 |

Setup compiles: 6 per arm (one pre-episode compile per function), counted separately from the internal
compiles that `invert-mutations` and `regalloc-search` perform inside one action. Repeat-after-no-effect
is 0 for every arm.

What this supports:

- **Procedural improvement, measured separately.** +0.233 acceptable rate over the base, 9 → 0
  redundant compiles and 3 → 0 missing-prerequisite calls on the panel; +0.167 on the held-out test
  surface. The redundant-compile result is the sharpest: the base model re-compiles a source the
  observation explicitly labels ALREADY COMPILED, nine times in thirty decisions, and the adapter never
  does. That is a rule that had to be read from the observation, and it is the same rule arm A
  implements mechanically.
- **Arm A's 6 premature stops are real.** The scripted order stops when its declared actions are
  exhausted, even when an intervening change has made a previously no-effect action legal again — which
  is exactly the case spec §2 warns about. Its 0.808 is not a ceiling on the panel.
- **No game-solving improvement.** 0 certified matches for all four arms. Procedure and outcome are
  separate claims; this run supports the first only.
- **Cost.** The adapter used fewer tokens than the base (399 vs 440 over 30 generations) but more
  internal compiles (13 vs 9), because it proposed more actions that moved the source and so triggered
  more of the controller's recompiles. Fewer tokens is not cheaper here.

## 5b. The bounded correction round (spec §5): run once, on TRAIN functions only

The adapter was run as the visiting policy on 8 TRAIN-split functions
(`.cache/recon/collect_corrections.sh`), its states snapshotted, its own choice graded by the same
mechanical rule, and every state where that choice was NOT acceptable recorded with the verified
corrective action (`eval/results/tool-action-20260921/corrections-train.jsonl`). Held-out functions were
never visited: `--split train` is the only split the script uses.

**The learned policy had one specific, repeatable mistake: 5 of 24 visited states proposed `diffrepair`
in a state with no diff**, so the action could only ever return `not-applicable`. The corrective action
was the same in all five (`invert-mutations` / `redraft` / `uopt-trace`, all applicable). C2 is the
first adapter retrained on the original 138 examples plus those 5 corrections (30 records, 143
examples).

| | C1 | C2 |
|---|---|---|
| missing-prerequisite calls, held-out panel | 3 | **0** |
| missing-prerequisite calls, held-out exercises | 0 | 0 |
| acceptable rate, panel / test surface | 0.833 / 0.917 | 0.833 / 0.917 |
| premature stops, panel | 3 | 6 |
| internal compiles, panel | 13 | 9 |

**The correction worked exactly where it was aimed and cost something elsewhere.** The targeted error
is gone on held-out functions, which is the strongest form this evidence can take — the corrections came
from train functions and the measurement is on dev. The aggregate acceptable rate did not move, and
premature stops doubled: C2 replaced `diffrepair` with `uopt-trace` and then stopped at slot 5 in all six
functions, where C1 had spent those slots. Both are reported rather than netted out. One bounded round
was permitted and one was run; it is not a general improvement, and it is not a second bite.

## 6. What remains, and what is not claimed

- **The correction round is done and is closed.** It ran once, on train functions, and its result is
  reported in §5b — including that it made one metric worse. A second round would be outside the
  spec's bound and would also start overfitting the panel it is measured on.
- **`regalloc-search` is still starved of `target_dump`.** It is wired to the real API and declines
  correctly, naming the missing input, but it has never actually searched on a real function. Its
  prerequisite is declared and the observation shows the decline.
- **One negative pilot is not a ceiling.** 6 functions at budget 5 cannot establish a game-solving
  limit, and no arm solved anything.
- **The registry trains orchestration of its existing actions.** It does not give the model a new
  source-editing or tool-authoring action; the adapter chooses among `compile`, `diffrepair`,
  `resolve-placeholders`, `invert-mutations`, `redraft`, `regalloc-search`, `uopt-trace` and `stop`.
- **`uopt-trace` now appears in adapter decisions** (it replaces `diffrepair` in C2's panel run) while
  remaining an observation-only action whose wiring is still unverified on real input.
- Nothing here promotes an adapter, writes to `src/`, or touches the match ratchet. No paid API was
  used; all inference was local.
- Sampling was greedy and deterministic, and both adapters reproduced the same decision sequences on a
  repeat run. Single-draw numbers on 12 exercises and 30 decisions carry wide uncertainty — the
  direction is consistent across two surfaces and two environments, and that is all that is claimed.

## 7. Exact commands

```bash
# splits (WSL, reads the KB)
cd /mnt/c/Code/gameDecomp && PYTHONPATH=$PWD python3 -m eval.tool_action_dataset \
  --mode splits --functions 60 --out eval/results/tool-action-20260921/splits.json

# procedural dataset, one surface per split (any Python)
python -m eval.tool_action_dataset --split train --out eval/results/tool-action-20260921/procedural-train.jsonl
python -m eval.tool_action_dataset --split dev   --out eval/results/tool-action-20260921/procedural-dev.jsonl
python -m eval.tool_action_dataset --split test  --out eval/results/tool-action-20260921/procedural-test.jsonl

# outcome-backed records from REAL episodes (WSL, needs m2c + the oracle)
bash .cache/recon/collect_tool_actions.sh          # FUNCTIONS=12 BUDGET=5 SNAPSHOTS=3 BRANCHES=3

# combine + manifest
python -m eval.tool_action_manifest \
  --inputs eval/results/tool-action-20260921/procedural-train.jsonl \
           eval/results/tool-action-20260921/outcome-train.jsonl \
  --out eval/results/tool-action-20260921/action-train.jsonl \
  --manifest eval/results/tool-action-20260921/dataset-manifest.json

# train (WSL, ~10 min)
bash .cache/recon/train_tool_action.sh

# evaluate: A scripted, B base, C adapter, plus the two ablation levels
bash .cache/recon/eval_tool_actions.sh
bash .cache/recon/eval_tool_actions_adapter.sh
python .cache/recon/tool_action_table.py

# tests
python -m pytest tests/test_tool_boundary.py tests/test_tool_action_dataset.py \
                 tests/test_tool_registry.py tests/test_tool_action_sft.py -q
```

## 8. Files

| file | role |
|---|---|
| `eval/tool_agent.py` | loop: state sync, compile contract, verdict retained, step -1, runner-failure and uncertified-exact guards, honest scripted policy |
| `eval/tool_agent_probe.py` | the ONE renderer: tool results, derived verification state, budgets, truncation contract, two ablation levels |
| `eval/tool_agent_compare.py` | model policy: shared renderer, token counters, ablation switches |
| `eval/tool_agent_run.py` | real context; hands the pre-episode verdict to the loop |
| `eval/tool_runners.py` | real runner APIs; `certified` marker on search exactness |
| `eval/tool_action_dataset.py` | rules, label sources, splits, replayable records, grading |
| `eval/tool_action_collect.py` | outcome-backed collection from real episodes |
| `eval/tool_action_manifest.py` | combination, alternative expansion, weights, manifest |
| `eval/train_tool_action_sft.py` | completion-only LoRA trainer + receipts |
| `eval/tool_action_eval.py` | three arms, two environments, mechanics grading, ablations |
| `tests/test_tool_boundary.py`, `tests/test_tool_action_dataset.py`, `tests/test_tool_action_sft.py` | 15 + 12 + 15 tests for the cases the spec names |

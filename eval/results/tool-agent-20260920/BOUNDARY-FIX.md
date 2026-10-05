# Tool-policy training: phase 1 (the observation/action boundary) is done

**Date:** 2026-09-20
**Spec:** `docs/deepseek-tool-policy-training-next.md`
**Tests:** `tests/test_tool_boundary.py` (13 new) + `tests/test_tool_registry.py` (19) â€” **32 passed**;
59 and 64 in the adjacent suites, unchanged.

## Defects reproduced and fixed

Each was reproduced first, fixed narrowly, and pinned by a test that fails without the fix.

| # | defect | fix | test |
|---|---|---|---|
| 1 | **`run_episode` called `policy.choose` BEFORE syncing `context.candidate`**, so at decision N+1 the policy still saw the source from before transform N â€” the parent at both decisions | state is synced before the decision; `Step.pre_action_sha256` records the source each decision was made from | `test_the_policy_sees_the_CHILD_at_the_next_decision`, `test_each_step_records_the_state_it_was_decided_from` |
| 2 | the observation dropped the actual tool result â€” no compiler errors, diffs, arguments or missing-prerequisite reasons | one renderer, shared by training and inference, emits `reason`/`error`/`stderr`/`diff`/`best_label`/`compiles`/â€¦ per step | `test_compiler_stderr_reaches_the_prompt`, `test_a_useful_tool_result_survives_rendering`, `test_a_missing_prerequisite_names_itself_in_the_prompt` |
| 3 | C truncated at 1,200 chars with no contract | truncation is stated in the text, with the total length | `test_truncation_is_stated_rather_than_silent` |
| 4 | `context.diff` never refreshed after a transform; a **latest-KB-diff fallback** supplied a verdict about a different source | the fresh diff replaces the stale one when a candidate changes; the KB fallback is **deleted**, not gated, because the query cannot establish source/target/recipe identity | `test_the_diff_is_refreshed_to_the_candidate_it_describes` |
| 5 | `_attempt_to_verdict` read `attempt.stderr` and guessed `profile`/`faults`; the real dataclass has `compiler_stderr` and neither of those | reads the verified fields; **invents no fault class** from a dataclass that has none | `test_the_verdict_reader_uses_the_real_attempt_fields` |
| 6 | `regalloc_search` runner assumed a dict return and a 1-arg callback; the real API takes `(source, label) -> Compiled` and returns an `Outcome` | wired to the real signature, constructing `rs.Compiled` | `test_regalloc_search_is_wired_to_the_real_two_argument_callback` |
| 7 | `redraft` returned `changed: True` unconditionally | change computed from source content | `test_an_unchanged_redraft_is_not_reported_as_a_change` |
| 8 | terminal actions were not stored as steps; raw model output truncated; steps omitted the pre-action source | `stop` is recorded as a step with its reason; `raw_response` retained; `pre_action_sha256` added | `test_a_terminal_action_is_recorded_not_merely_obeyed` |
| 9 | `uopt_trace` runner probed guessed callable names and crashed the episode | resolves the entry point, reports `not-applicable` **naming what the module exposes** | `test_a_missing_prerequisite_names_itself_in_the_prompt` |

## The two that mattered most

**Defect 1 is the one that made every transcript unusable as training data.** A label attached to the
wrong input teaches the wrong mapping, and it would have been invisible: the transcripts looked
well-formed and the runs completed. It was found by a read-only probe, not by a test, which is why it
now has one.

**Defect 2 is the one that made a negative result uninterpretable**, and the first attempt to fix it
was INCOMPLETE in a way that mattered. See the correction below.

## CORRECTION (2026-09-21): the re-run below was still made against the broken prompt

`observation()` was taught to emit the tool result and every renderer test passed -- because the tests
called `observation()` directly. `ModelPolicy._render`, one call earlier, still projected history to
`action/status/changed/exact` and passed that in. During the re-run below the model therefore still
read `diffrepair -> no-change` with no reason, no diff, no arguments, and an unknown budget. Replaying
the old projection renders the step line as `compile {} -> failed` with the compiler's error text
absent, which is checked in `tests/test_tool_boundary.py`.

**So the table and the conclusion below do not hold.** The negative did not survive the repair; it was
measured against the same defect. A renderer test must go through the policy, never around it:
`test_the_model_policy_prompt_carries_the_tool_result` now does.

Two further defects found while building the dataset on top of this discarded the observation as well:
the loop's auto-compile kept only `exact` and `certificate_status`, dropping the fresh diff, the
compiler's stderr and the score; and `build_context`'s pre-episode compile -- which FAILS on most
collected functions, so its stderr is the entire residual -- was never recorded as a step, so the
first decision was made from a state the policy could not see. Both are fixed; the controller's
initial verdict is now step -1.

## Explicitly not claimed

- **No adapter was trained.** Phases 3â€“5 of the spec (verified observationâ†’action dataset, receipts
  pipeline, completion-only LoRA, A/B/C evaluation) are not started. **SUPERSEDED 2026-09-21**: the
  dataset, the adapter and the paired evaluation now exist -- see
  `eval/results/tool-action-20260921/REPORT.md`.
- **Defect 6 is wired but unexercised**: `regalloc-search` still has no `target_dump` in the context,
  so it declines on every real function. The runner now matches the real API, but whether it *works*
  is untested until that input is supplied.
- **Defect 5 is partial.** `Attempt` carries no fault classification and `solver.signals` is the
  supported path to one; until that is wired the residual is the diff plus `compiler_stderr`, which is
  what the oracle actually returned. No fault profile is fabricated.
- The registry trains **orchestration of its existing actions**. It does not give the model a new
  source-editing or tool-authoring action.
- Compilation IS now contracted to one rule (spec item 5), and the prompt states it: the controller
  compiles the initial candidate and every source a transform produced, the whole verdict is attached
  to the step it describes, the observation says whether the current source carries a verdict, and the
  scripted policy follows the same rule. It was NOT contracted when the re-run below was made.

## Live verification: the re-run below is superseded

Kept for the record. Read it as a measurement of the still-broken prompt, not of the policy.

The head-to-head was re-run on the same 12 functions, budget 7, with the state sync fixed and the
renderer only half-fixed (`head-to-head.json`):

| | scripted | model, broken interface | model, **still-broken interface** |
|---|---|---|---|
| certified matches | 0 | 0 | **0** |
| candidates produced | 13 | 15 | 13 |
| generations | 0 | 78 | 84 |
| invalid proposals | â€” | 0 | **0** |

**The negative did NOT survive the repair -- there was no repair yet.** The spec's premise was that a
negative could not be attributed to the policy while the model was being shown
`diffrepair -> no-change` with no reason, no diff, no arguments, an invisible initial compile and an
unknown budget. That is still what it was being shown, so this table is a measurement of the prompt.

- **`osGetThreadPri` proposed `redraft` four times consecutively** â€” every one `no-change`.
- `noopFourArgs` proposed `resolve-placeholders` four times, all `no-change`.
- `initMainMenuSceneModelRenderer` proposed `redraft` three times; `__osPopThread` proposed
  `diffrepair` three times, all `no-change` or `not-applicable`.

Repeat rate is essentially unchanged (~15% before, ~18% after) -- unsurprising, since the prompt was
unchanged too. The behaviour is real and worth recording, but it is evidence about a policy that
cannot see the results, not about one that can. It is the behaviour the paired evaluation is built to
re-measure with the results actually present.

Also visible: the scripted arm now records its terminal `stop` on `Fviboff` (defect 8 fixed, live),
and the model again reached for `uopt-trace`, which the script never uses.

**Still not claimed:** that this is a model ceiling. 12 small functions with `regalloc-search` starved of its `target_dump` and no compilation contract is a narrow test, and neither arm closed anything. The honest statement is that on this panel the policy limitation is now *observable* rather than inferred - not that it is intrinsic.

## What remains, in spec order


1. **The compilation contract** (spec Â§1 item 5): decide whether the controller guarantees a fresh
   compile of the current source and expose it, or expose an explicit unverified state. Prompt and
   runtime must agree.
2. **`target_dump`** into the context so `regalloc-search` can run, and refresh `target_dump`/cost
   accounting for nested tool work (spec Â§6: one search action can cost far more than one inspection).
3. **Verification levels** (spec Â§2): protocol / state-procedure / task-outcome as separate labels,
   including the "same tool after another transformation is legitimate" rule.
4. **The dataset** (spec Â§3): procedural exercises with paired observation changes, plus
   execution-backed branch comparisons from independent copies at equal budget. Audit the claimed 133
   sibling preferences for genuinely identical observable state before using them.
5. **Receipts** (spec Â§4) â€” mostly present via `Step`, still missing tool schema/renderer versions and
   per-step cost accounting.
6. **Train + evaluate** (spec Â§5â€“6): one bounded LoRA with assistant-action-only loss, then A/B/C.

Reproduce phase 1 with `bash .cache/recon/test_boundary.sh`.


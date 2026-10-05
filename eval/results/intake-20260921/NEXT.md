# What to do next, and where the evidence stands

> **SUPERSEDED IN PART, 2026-09-21.** The table below reads "20 of 200 converted, 16 frontend-passing" and
> that was correct when written. The same frozen frame now measures **32 IDO / 19 IDO+frontend / 2 exact**
> with **zero draft-hash drift**, and the three levels are reported separately on every row
> (`wide-intake-acceptance2.json`, schema 5). The follow-up round — the audit's control failures, the
> development set exported off the frontend-passing candidates, and a bounded branching search over the
> same catalog — is written up in `eval/results/audit-fixes-20260921/REPORT.md` with its numbers in
> `ROUND.json`. Everything below about *method* still holds; the *counts* are superseded.

Rewritten 2026-09-21 after executing the previous version, fixing nine defects, and building the instrument
the earlier claims were missing. Full detail: `REPORT.md`; per-defect numbers: `FIXES.json`, `FIXES2.json`;
the instrument round: `GOAL-ROUND-1.json`, `INSTRUMENT.json`.

## The answer to "tooling or training"

**Tooling, and not as an opinion — 9 defects for 9, all mechanical.** Every one was a fallback, a default,
a comparison, a missing field, or an INPUT nobody passed. None was a missing model capability — including
the clang frontend, which looked like a missing capability and turned out to be a missing input.

The training question is now answerable rather than blocked: the 200-state frame resolves a 4-state effect
across builds and a 2-state effect between arms. It is still the wrong next step, because the action space
has one useful lever (`resolve-placeholders`) and a policy with one lever has nothing to choose between.

## Where the evidence stands

| claim | status | receipt |
|---|---|---|
| Observation/state boundary repaired (real tool results, fresh candidate, budgets) | established | `eval/results/tool-action-20260921/REPORT.md` |
| Procedure learned by the action adapter (0.600 -> 0.833 acceptable, 9 -> 0 redundant compiles) | established | same |
| **Certified matches improved by that training** | **NOT established — 0 in every arm** | same |
| Intake sequence converts non-compiling drafts | **20 of 200 (10.0%), 2 certified**; the earlier 22.5% was a narrow-frame composition artifact | `wide-intake.json`, `instrument-standardisation.json` |
| The action dataset was composed of states where no action helps | **FALSE — 16 of 200 improve, 7 compile, 1 certified** | `wide-control.json` |
| `m2c_placeholders` covers `?` in parameter lists | was FALSE, now fixed | `FIXES.json` |
| The clang frontend is a missing capability | **FALSE — a missing INPUT**, now wired; 177 of 180 blocked states gain a reachable repair mechanism | `frontend-step-receipt.json` |
| `rmonPrintf` is an uncounted match | **FALSE — one of 12 already `matched` and in `src/`**; the probe cannot see that class | `boundary-decision.json` |
| Two-generation improvement (L2/L3) | not attained / not implemented | `eval/results/narrow-rsi-20260921/REPORT.md` |

**The pattern, restated from measurement.** Nine defects, and every one was a fallback, a default, a
comparison, a missing field, or an input nobody passed: a regex that required punctuation after a token m2c
writes before a name; a running order that omitted the campaign's own first step; a runner comparing two
different producers' output; a runner trying five entry-point names on a module that is a parser; three
declared `Context` fields nobody populated; a header added for the name the draft itself defines; a
`detail` field written by every runner and thrown away by the harness; a checker recipe cached without the
target it is a projection of; and a `? name` pattern that could not match a parameter. The capability was
present in every case and the wiring missed.

## Steps 1–3 are DONE. What they found, and what is actually next.

All three closed in the round recorded by `GOAL-ROUND-1.json`. The results changed the picture enough that
the old step list is replaced rather than amended.

### The frame is now 200 states, and it says the lever is a fifth the size the narrow frame suggested

```
intake, fresh build         20/200 converted  2 exact   10.0%
intake, frozen replay       20/200 converted  2 exact   10.0%   <- deterministic
control                     16/200 improved   7 compiling  1 exact   8.0%
```

Direct standardisation settles why: the wide frame's per-tier rates applied to the narrow frame's tier
counts give **0.2248 against the narrow frame's observed 0.2250**. The two rates are the same rate; the
narrow frame simply held far more of the tiers where the lever works. Per-tier conversion runs
**1.000 / 0.500 / 0.294 / 0.070 / 0.047** from tiny to huge — a cliff after `small`, not a gradient.

Both arms now state their sensitivity, and they distinguish the two comparisons: `across_frames` carries
membership drift (20 states at n=200), `arms_on_one_frozen_frame` does not (2 states).

### The clang frontend is wired, and it was a missing INPUT rather than a missing capability

`solver/frontend_diagnostics` + the `frontend_diagnostics` intake step, second in the sequence — after the
placeholder rewrite, because `?` is not C to clang either and with the token present the checker stops at
the same wall cfe does.

* **Wiring is safe:** 20 converted / 2 exact with and without the step, same 200 functions, 0 differing
  drafts. It is an observation and returns `changed: False`.
* **Wiring is worth it:** cfe reports a mean of **2.0 errors** per blocked state and truncates; clang
  reports every independent blocker, and **177 of 180 blocked states** now make at least one repair
  mechanism reachable that cfe could not name — `undeclared_identifiers` 172, `scalar_header_prototypes`
  160, `negative_field_repair` 46, `void_field_repair` 23.

Cost: 0.06s per state, about 12 seconds over 200 states.

### `rmonPrintf` is not an anomaly and there is no counting defect

12 functions have a passing ROM-backed function-extent certificate while `attempt.exact` is false — and
**all 12 are already `matched` in the KB and present in `src/`**, `rmonPrintf` included, at
`src/ending/ending_credits_ui.c`. They are promoted through the campaign path
(`completion_campaign.py:210` → `function_exact_pending_integration` → `prepare_integration`).

**Decision: do not widen `Attempt.exact`.** The boundary certificate excludes trailing text bytes, TU/link
layout and the whole ROM on purpose and declares `requires_isolated_integration`; `Attempt.exact` gates the
ratchet. What is true instead is a property of *this probe*: keyed on `attempt.exact`, its "certified
matches" is a lower bound, not the count of promotable functions.

## What is next

**Step A — the `void` defect in struct planning: a decision is needed, not more analysis.**

`solver/typedecl.plan` skips pointer parameters whose type is in `PRIMITIVE_TYPES`, and `void` is in that
set. m2c writes **every** untyped parameter as `void *`, so the one type `opaque_variant` exists to handle
is the one type `plan` refuses to plan. Reproduction (`_plan_isolate.py`):

```
plan('s32 f(void *arg0) { return arg0->unkC; }', layout={'param0': [(12,4,'s32')]})
  as shipped          -> 0 plans
  with void demoted   -> 1 plan, text: typedef struct { char pad00[0xc]; s32 unkC; } void;
```

`} void;` is invalid C, which is why the diagnostic is `Selector requires struct/union pointer` and never
`expected identifier` — the invalid struct is never emitted. **The fix is two-part and both halves are
needed**: demote `void`, *and* give the generated typedef a real tag while respelling the parameter. The
second half means deciding which struct a `void *` parameter should be, which is a semantics decision the
project's own rule ("unknown is the default") reserves for a human. Shipping the demotion alone would emit
invalid C — demonstrated, not predicted.

Worth knowing when deciding: `opaque_variant` fires on **38 states and the chain gets longer** (1.79 →
1.87 classes), and it has never been credited with a conversion. It is currently the only step in the
sequence that does not pay for itself.

**Step B — `incomplete definition of type 'X'` is the same blocker wearing a different name.**

`member-on-typed-pointer` and `unclassified` both reduce to it: the actor struct has no visible body, so
`arg0->member` cannot resolve. All 12 actor types ARE defined in the repo, inside the `src/*.c`
translation units, with offsets annotated in comments — and the binary corroborates at least one (a byte
access at 0x44 against `/* 0x44 */ s8 transformDirty;`). Making those declarations available is a real
route but shallow on this data: of the first 14 blocked states only 2 had a matching type name, and one of
those **contradicted** the binary (`state` annotated 0x1c against offsets 24, 26, 44..56).

If it is pursued, hold it to the corroboration rule: `src/**` is the reference decomp, a struct
DECLARATION is shared vocabulary (`include/**` already supplies it and such matches are tiered
`header-assisted`, not `SOLVED`), a function BODY is the answer — and an offset the binary contradicts is a
guess, not evidence.

**Step C — the ranking is now trustworthy.** 62 states carry exactly one defect class: `undeclared-identifier`
30, `unclassified` 14, `member-on-typed-pointer` 13. Read at the end of the sequence, which is the only
place it means anything.

**Step D — then train**, on the reachable set only.

## What not to do

* Do not read a residual ranking taken from **any single point** in the pipeline. That is now demonstrated
  twice: cfe's first error, and the frontend at position 2 of 7. The chain is 2.49 classes long on average
  and a 6-step sequence moves it; read it where the candidate is, or do not rank it.
* Do not ship half of the `void` fix. It emits `} void;`.
* Do not quote 22.5%. It was a composition artifact; the same code converts 10.0% on a frame drawn like the
  tiers actually are. And it is a *compiles* rate: 2 of 200 certified.
* Do not quote a rate from a `--frozen` replay without checking frame identity. Fresh builds still move
  (4 of 40 once), because the pool is a live query against a KB a campaign writes.
* Do not train the chooser yet. Five measurements say it is not the constraint, and the action space has
  one useful lever and nothing to choose between.
* Do not assume a module that declines is a module that is broken — but do not assume it is fine either.
  `negative_field_repair` looked dead and was gated on a diagnostic nothing produced; `opaque_variant` was
  *documented* as the owner of a class and has never produced valid output for it.
* Do not read a harness's "certified matches" as the pipeline's. 12 functions certify at function-extent
  level and are already matched; `attempt.exact` cannot see them.

## Known unknowns

* `rewrite_do_while` is gated out on all 200 states: the `contains a do-while loop` diagnostic it waits for
  never occurs on this class, so it is dead weight in the sequence. Whether it fires on any other class is
  unmeasured.
* `globals_variant` never fires with a change on this frame. Its motivating class may be absent here.
* The commonest first clang error is `expected parameter declarator` (20 states), then `unknown type name`.
  Neither is a whole-distance class on any state, so both sit behind something else.
* The frame's drift figure (4 of 40) is observed once, on one class, with a campaign running.

## Receipts

* `eval/results/intake-20260921/GOAL-ROUND-2.json` — this round, every number, and what was not shipped
* `eval/results/intake-20260921/chain-trace.json` — which step shortens the chain, read after every step
* `eval/results/intake-20260921/_plan_isolate.py` — the `void` reproduction, 0 plans vs invalid C
* `eval/results/intake-20260921/GOAL-ROUND-1.json`, `INSTRUMENT.json` — the 200-state frame and its properties
* `eval/results/intake-20260921/instrument-standardisation.json` — 0.2248 vs 0.2250
* `eval/results/intake-20260921/frontend-step-receipt.json` — what the frontend step does and does not do
* `eval/results/intake-20260921/boundary-decision.json` — the 12, and why `Attempt.exact` stays as it is
* `eval/results/intake-20260921/{wide-frame,wide-intake,wide-intake-traced,wide-control}.json`
* `eval/results/intake-20260921/REPORT.md`, `FIXES.json`, `FIXES2.json` — the earlier work in full
* `eval/results/tool-action-20260921/REPORT.md` — interface repairs, adapter, paired evaluation
* code: `solver/m2c_placeholders.py`, `solver/m2c_negative_offset.py`, `solver/compile_recovery.py`,
  `eval/intake_probe.py`, `eval/intake_control.py`, `eval/intake_runners.py`, `eval/tool_runners.py`,
  `eval/tool_agent_run.py`, `eval/tool_registry.py`
* tests: `tests/test_m2c_negative_offset.py`, `tests/test_m2c_placeholders.py`, `tests/test_intake_frame.py`,
  `tests/test_tool_boundary.py`
* recon: `.cache/recon/{class_frame,class_control,post_fix_measure,post_fix2_measure,verify_fixes}.sh`



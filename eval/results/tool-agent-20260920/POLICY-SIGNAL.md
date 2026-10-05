# Training a tool-selection policy: the interface was the whole gap

**Date:** 2026-09-20
**Question:** can the model be trained on how it responds to the tools?
**Answer, after measuring:** the model already responds well. What it could not do was *format* the
response, and that cost one line to fix. The transcripts still cannot teach selection, and the
corpus signal is thin — but neither of those was the binding constraint.

## The decisive measurement: base model, zero-shot, no adapter, no training

`eval/tool_agent_probe.py`, 12 unsolved game functions, greedy decoding, the action space in the
system prompt.

| | no prefill | with an assistant `{` prefill |
|---|---|---|
| parsed as JSON | 0 / 12 | **12 / 12** |
| validated as a legal action | **0 / 12** | **12 / 12** |

Without the prefill every reply arrived as ```` ```json … ``` ```` and `json.loads` rejected all
twelve. That 0.000 was measuring the prompt harness, not the model — and `solver/llm.py` already
documents the fix ("primed with an opening fence … every generation path in the project passes one").
The probe simply had not passed one.

## And the choices were already good

| action chosen | count |
|---|---|
| `resolve-placeholders` | **8 / 12** |
| `compile` (with `{"save": true}`) | 3 / 12 |
| `redraft` | 1 / 12 |

**The base model, zero-shot, picks `resolve-placeholders` first on eight of twelve functions.** That
is the one action the scripted run found actually closes matches — it changed the source on 3
functions and closed 1, a 33% hit rate, while `redraft` changed 22 and closed none.

The model found the highest-yield action from the prompt alone, with no training, no transcripts and
no adapter. It also produced a legal non-default parameter (`save: true`) unprompted, and it never
proposed an action twice.

## What this changes

1. **The interface is not a research problem.** It is a prefill, and it is solved.
2. **The scripted order is already beatable, and by an untrained model.** `ScriptedPolicy` tries
   `invert-mutations` and `resolve-placeholders` in the wrong order; the model goes straight to the
   one that works. Comparing the two at equal budget is now the highest-value experiment available
   and it needs no training at all.
3. **My earlier "133 pairs is too thin to train selection" was answering the wrong question.** It is
   still true for *fitting* a policy on preference pairs. But the base model already selects
   sensibly, so the bar a trained policy must clear is much higher than I implied, and there may be
   less to train than either of us assumed.

## What is still true

The 25 transcripts cannot teach selection: first-action entropy 0.000 bits, one certified match. And
the corpus's parent-relative signal is real but confounded, with improvement rate and exactness
pointing in opposite directions — the two highest-improving strategies produced zero exact matches
between them. Neither of those claims changes.

## The signal that does exist

`attempts.parent_attempt_id` gives a parent-relative label: did this attempt beat the one it refined?
Across **8,132** parented attempts where both sides scored:

| outcome | count | share |
|---|---|---|
| improved | 484 | **6.0%** |
| same | 3,200 | 39.4% |
| **worse** | 4,448 | **54.7%** |

**More than half of all refinements make the score worse**, which is the base rate any policy is
fighting.

Improvement rate by strategy, for the 24 strategies with n ≥ 40:

| strategy | n | improved | rate | exact |
|---|---|---|---|---|
| `compile-intake:project-header` | 42 | 14 | **0.333** | 0 |
| `typed-semantic-gradient-beam` | 486 | 146 | **0.300** | **0** |
| `modelrepair-d1` | 72 | 16 | 0.222 | 4 |
| `m2c-project-header-intake` | 52 | 11 | 0.212 | 4 |
| `differential-debugger-causal-repair` | 228 | 44 | 0.193 | 0 |
| `differential-deterministic-statement-ord` | 116 | 12 | 0.103 | **5** |
| `m2c-semantic-seed` | 437 | 24 | 0.055 | **5** |
| `repair-d1` | 168 | 7 | 0.042 | 0 |
| `do-ban-rerun:zero-token-m2c-harvest` | 75 | 2 | 0.027 | 0 |

**The best strategy improves 5.5× more often than the base rate** (0.333 against 0.060). That is a
large, learnable effect, and it is an order of magnitude more data than any training set built so far.

## The trap inside it, which decides the training set

**Improvement rate and exactness are not the same target, and here they point in opposite
directions.** The two highest-improving strategies — `compile-intake:project-header` (0.333) and
`typed-semantic-gradient-beam` (0.300, n=486) — produced **zero exact matches between them**. The
strategies that actually produced certified matches are the middling ones:
`differential-deterministic-statement-ord` (5 exact at a 0.103 rate), `m2c-semantic-seed` (5 at
0.055), `m2c-project-header-intake` (4 at 0.212).

A policy trained on improvement rate would learn to reach for the strategies that move the score
without ever closing a function. The objective has to be exactness, and improvement is a diagnostic —
the same distinction the brief drew at the outset.

**And the comparison is confounded.** A strategy's rate mixes its own merit with the difficulty of the
functions it was tried on, and strategy choice is not random: `typed-semantic-gradient-beam` appears
486 times and `do-ban-rerun` 75, and nothing here controls which function each was applied to.

## The training set this implies, and how big it actually is

Not cross-strategy averages over the corpus. **Paired, within-function comparisons**: the same
parent residual, two different actions, one improved. That is the only form in which the function's
difficulty cancels, and it is what a policy needs in order to learn *choosing* rather than
*attempting*. Measured on the KB:

| | |
|---|---|
| parents with a scored child | 2,306 |
| parents where **two or more distinct strategies** were tried | **487** |
| of those, **discordant** — one improved and another did not | **133** |

**133 usable preference pairs**, and they are unconfounded by construction: same parent, same
residual, two actions, one label. That is the same order as the 139 verified pairs the lineage audit
found across 8,132 edges, and it is the shape `eval/distill_policy.py` and `eval/train_policy.py`
already consume — technique-labelled pairs into DPO.

**133 is enough to fit a small policy and not enough to trust one.** It is the right dataset shape,
it is five times the usable signal in the transcripts (which is zero), and it is honest to say that
the binding constraint is now data volume rather than method. The cheap way to raise it is named in
the previous section: run more actions on the same functions, which is exactly what the action-space
agent does and what the scripted run has only just started producing.

## What I am not claiming

No adapter was trained here. Building a trainer for a 25-episode, 0-entropy, 1-success dataset would
have produced a number that looked like learning and was not, and there is already a
`distillation`/`distill_policy` path in this repository whose gate exists precisely to refuse that.

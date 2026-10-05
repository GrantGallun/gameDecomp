# SUPERSEDED — do not distil from this run's gate verdict

**Date of this note:** 2026-09-20

`replay-b24.json` in this directory stores `gate.passed: true` and `distillation.gate_passed: true`,
and `pairs.jsonl` holds the 2,639 preference pairs that gate authorised.

**That verdict was computed under gate rule version 1, which could not see the problem.** It
compared the controller (`adaptive`) against `fixed-seed` and `random` on discovery only. Neither
control has the controller's priority terms, so neither can separate the *adaptive allocation* from
the *learned strategy memory* inside it. The control that can — `AdaptiveNoLearn`, the same priority
terms with learning switched off — was already implemented in `eval/research_loop.py`, and its
docstring states the criterion:

> "If it matches `Adaptive`, the priority terms are doing the work and the strategy memory is
> decoration; if `Adaptive` wins, the loop is learning research taste without touching any weights,
> which is the mechanism worth distilling."

It matched. This run's own report records `adaptive` and `adaptive-nolearn` at **identical** rates on
both metrics — 0.985 discovery and 0.345 solved — which by that criterion means there was nothing
there to distil.

## What is still valid here

- **`pairs.jsonl` is not corrupt.** It is a record of which logged decisions led to what, labelled by
  realized value. That labelling does not depend on the gate.
- **The allocation finding stands.** `adaptive` beating `fixed-seed` and `random` on discovery is
  real, and reproduces (see below). What does not stand is the inference that the *learning*
  component contributes.

## What replaced it

`eval/results/research-loop-20260920/GATE-REVIEW.md`: the same replay re-run at budgets 8, 24 and 48
under gate rule version 2, which adds the no-learn control as a **paired** comparison on solved
functions.

Result: `adaptive` and `adaptive-nolearn` solved **exactly the same functions** at every budget —
zero discordant pairs — and the gate now **refuses** at all three. Recorded as REFUTED
(`patterns/hypotheses.json`, `strategy-memory-beats-its-own-no-learn-control`) and in
`memory/hypothesis-graveyard.md`.

`eval/distill_policy.require_gate` no longer reuses a verdict from an older rule; it recomputes. So
the stored `passed: true` above can no longer authorise a weight update, which is the only reason
this note is not merely advisory.

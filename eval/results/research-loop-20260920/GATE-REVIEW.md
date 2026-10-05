# The research-policy gate reviewed: rule 2 refuses, on three budgets

**Date:** 2026-09-20
**Artifacts:** `replay-b8.json`, `replay-b24.json`, `replay-b48.json`
**Supersedes the verdict in:** `eval/results/research-loop-20260916/replay-b24.json`
**No GPU, no model, no weight update.** The replay is off-policy over the knowledge base that already
existed.

## What was wrong

`eval/research_loop.py`'s controller chooses where the next unit of compute goes. The design's claim
is that the *learned* part — a strategy memory whose seed weights update from realized value — is
worth distilling into model weights. `gate()` was the stopping condition: do not spend GPU hours
distilling a controller that is no better than its controls.

It compared `adaptive` against **`fixed-seed` and `random` only**, on **discovery**. Neither control
has the priority terms, so neither can separate the *allocation* from the *learning inside it*. The
module already contained the control that can — `AdaptiveNoLearn`, the same priority terms with
`learn=False` — and stated the criterion outright:

> "If it matches `Adaptive`, the priority terms are doing the work and the strategy memory is
> decoration; if `Adaptive` wins, the loop is learning research taste without touching any weights,
> which is the mechanism worth distilling." — `research_loop.py`, `AdaptiveNoLearn` docstring

`gate()` never called it. So the Sept 16 replay stored `passed: true`, and `eval/distill_policy.py`
— whose first refusal is "refuses to train when the gate says the controller did not beat fixed-seed
and random reseeding" — was satisfied, and wrote **2,639 preference pairs** with
`distillation.gate_passed: true`.

## What changed

`gate()` now tests three conditions and carries `rule_version: 2`:

| | condition | why |
|---|---|---|
| R1 | `adaptive` beats `fixed-seed` on discovery | as before |
| R2 | `adaptive` beats `random` on discovery | as before |
| **R3** | `adaptive` beats **`adaptive-nolearn`** on **solved** functions, **paired** | the only comparison that isolates the learned component |

R3 is paired (`n01` = solved only by `adaptive`, `n10` = solved only by the control) rather than a
rate difference. Over 400 functions one function is 0.0025 of a rate, so an unpaired margin can be a
single coin-flip dressed as a finding; `n01 > n10` cannot be. `evaluate()` now records
`solved_functions` and `discovered_functions` per policy so the paired comparison is possible at all,
and a report carrying only rounded rates is **refused** rather than passed by default.

`distill_policy.require_gate` no longer reuses a stored verdict from an older rule — it recomputes.
A verdict is only as good as the rule it was computed under, and the stored one here was exactly the
kind that goes stale when the rule changes.

## The re-run: identical decisions, three budgets

400 forests, `min-attempts 3`, the current `kb-sbk1.sqlite`.

| policy | b=8 P_disc / P_solved | b=24 | b=48 |
|---|---|---|---|
| random | 0.9775 / 0.2200 | 0.9875 / 0.2275 | 0.9925 / 0.2325 |
| fixed-seed | 0.9825 / 0.2200 | 0.9950 / 0.2325 | 0.9975 / 0.2350 |
| greedy | 0.9825 / 0.2200 | 0.9950 / 0.2325 | **1.0000 / 0.2375** |
| evolutionary | 0.9825 / 0.2200 | 0.9925 / 0.2300 | **1.0000 / 0.2375** |
| **adaptive-nolearn** | 0.9925 / 0.2300 | **1.0000 / 0.2375** | **1.0000 / 0.2375** |
| **adaptive** | 0.9925 / 0.2300 | **1.0000 / 0.2375** | **1.0000 / 0.2375** |
| **gate (rule 2)** | **refused** | **refused** | **refused** |

Paired solved outcomes — `adaptive` vs `adaptive-nolearn`:

| budget | n01 (only adaptive) | n10 (only no-learn) | both | functions |
|---|---|---|---|---|
| 8 | **0** | **0** | 92 | 400 |
| 24 | **0** | **0** | 95 | 400 |
| 48 | **0** | **0** | 95 | 400 |

**The two policies are identical on every metric at every budget, and they solved exactly the same
set of functions — zero discordant pairs either way.** The learned seed weights never reach a
decision that changes the outcome. By the module's own criterion, the strategy memory is decoration,
and there is nothing there to distil.

Two further observations from the same runs:

- **At budget 48 the allocation stops mattering too.** `adaptive`, `adaptive-nolearn`, `greedy` and
  `evolutionary` all reach P_disc 1.0000, P_solved 0.2375, mean_regret 0.000. At this horizon the
  logged forests are exhausted, so every reasonable policy finds the same answer.
- **The search space is almost entirely dead.** `mean_dead_share` is 0.996–0.999 for every policy at
  every budget: ~99.8% of expansions reveal no improvement. That is why the policy differences are
  compressed into a band of a few functions.

R1 and R2 still pass — `adaptive` beats `fixed-seed` and `random` on discovery at all three budgets.
So **"adaptive allocation beats random allocation" survives; "the learning contributes" does not.**
Those are different claims, and only the first is evidenced.

## Why these numbers differ from Sept 16

The Sept 16 report recorded `adaptive` 0.985 discovery / 0.345 solved; today's budget-24 run records
1.0000 / 0.2375. The KB has grown since — `load_forests` takes the 400 largest forests with at least
`min_attempts` — so this is a re-run on the current KB, not a replay of the same data. What is
consistent across both snapshots and all three budgets is the one thing the review rests on:
`adaptive` and `adaptive-nolearn` are indistinguishable.

## Follow-up: the memory is LIVE but NOT outcome-relevant, and the reason is structural

Two readings were left open above. Measuring the weight trajectory killed one of them and a
decision-level comparison answered the other. Neither original reading was right.

### The weights do move — reading 2 is dead

`SeedBook` persists across episodes by design and `evaluate` drives one policy across every forest,
so the trajectory is observable from outside with the module's own API (`seedbook-trajectory.json`,
400 forests, 7,388 expansions):

| seed | uses | value | weight before | after | Δ |
|---|---|---|---|---|---|
| `unexplored` | 26 | 1969.71 | 1.0000 | **2.9996** | +1.9996 |
| `control-flow` | 4 | 24.90 | 1.0000 | 1.5583 | +0.5583 |
| `register-pressure` | 7 | 30.07 | 1.0000 | 1.4619 | +0.4619 |
| `statement-order` | 1 | 3.33 | 1.0000 | 1.1000 | +0.1000 |
| `field-layout` | 2 | 1.11 | 1.0000 | 1.0283 | +0.0283 |
| `contradiction`, `literal-value`, `symbol-binding` | 0 | 0 | 1.0000 | 1.0000 | 0 |

40 `credit()` calls, 0.541% of expansions, 5 of 8 seeds moved, the largest to its ceiling. The seed
term contributes `W_SEED · w / 2` = 0.075–0.225 — a spread of **0.150**, the same order as
`W_UNCERTAIN` (0.15) and most of `W_UPSIDE` (0.25). So the term is not negligible in magnitude.

### But it changes no outcome — and it does change decisions

`decision-divergence.json`, comparing the two policies node by node on the same 400 forests:

- **12 of 400 forests (3%) have divergent decision sequences**, first divergence at steps 1–22,
  median 9.
- **0 functions differ in the solved set** (n01 = 0, n10 = 0).

So "changes no outcome" and "changes nothing" are different claims, and the first is true while the
second is false. The memory is live; its decisions are simply not the ones that decide the outcome.

### Why, measured rather than argued

The three reasons originally given here were speculation. Two of them were **our own defects**, and
fixing them produced the actual answer.

**Defect 1, fixed: the credit signal was confounded.** `credit()` updates a *preference* weight, but
`_reseed` assigns `contradiction` and `unexplored` by plateau **rule**. Crediting those taught the
memory that whatever the rule already hands out is valuable — circular, and it is why `unexplored`
reached its ceiling weight 3.0 on a mean credited gain of 75.8/use while influencing 12 decisions in
400 forests. `Branch.by_preference` now records where a seed came from, and only a preference-chosen
seed is credited.

**Defect 2, fixed: the documented decay was never wired.** `credit()` had one call site, inside a
GAIN test, so weights were monotonically non-decreasing and "one that keeps appearing on dead
branches decays" could not happen. Non-gains now credit 0.0, the neutral pull the docstring
describes.

**Fixing both made the tie exact, and that is the finding.** With the decay wired, `credit` fires on
all 7,388 expansions, so ~40 gains are diluted by ~7,348 dead credits and every weight sits at
exactly 1.0 — which makes `adaptive`'s seed term *identical* to `adaptive-nolearn`'s. The corrected
mechanism does not fail; it declines to move on noise.

**The measured reason, over the seeds that were used:**

| seed | uses | gains | gain rate |
|---|---|---|---|
| `field-layout` | 328 | 2 | 0.00610 |
| `control-flow` | 679 | 4 | 0.00589 |
| `unexplored` | 4450 | 26 | 0.00584 |
| `statement-order` | 208 | 1 | 0.00481 |
| `register-pressure` | 1576 | 7 | 0.00444 |

**The gain rates span 1.37× on counts of 1 to 26 events.** A rate estimated from one or two events is
not measured, and no weighting scheme can separate seeds whose rates are indistinguishable. So the
strategy memory is not "decoration" and not "too small a term": **there is no signal in the seed
dimension to learn from at this resolution.** Three of eight seeds were never used at all, so their
rates are undefined rather than zero.

That reframes the work as a resolution problem with arithmetic attached. At a ~0.5% gain rate,
7,388 expansions yield ~40 gain events spread over 8 seeds. Estimating a per-seed rate to within
~20% needs roughly 25 events *per seed*, i.e. **~5–10× the current budget** — or fewer seeds, or a
denser success signal. Until then, `gate()` refusing is the correct behaviour, and a weight that
moved would be fitting noise.

This is our error to fix, not a property of research-policy learning.

### The half-wired rule, confirmed

`seedbook.credit` is called at exactly one site (`research_loop.py:609`), inside
`if after > before + PLATEAU_EPSILON:`. So the argument is always a positive gain, `target =
1.0 + clamp(gain/10, −0.5, +2.0)` is always > 1.0, and the weights are **monotonically
non-decreasing** from 1.0 — confirmed in the trajectory (no weight below its start; 0 decays). The
`max(−0.5, …)` branch is unreachable, and the docstring's "one that keeps appearing on dead branches
**decays**" cannot happen. Whether wiring it would change anything is now doubtful for reasons 1–3
above, but the docstring and the code disagree, and that is a defect either way.

### Verdict: retire the capability claim, keep the heuristic

**Not fixable by tuning**, and the negative result is more interesting than the mechanism was.
`adaptive` beating `fixed-seed` and `random` on discovery is real and reproduces; that is the
*priority terms*, and it should be kept and reported as an allocation heuristic. What must be
retired is the claim that the loop is "learning research taste without touching any weights" —
on this data it is not, and it cannot be, because the outcome does not depend on the allocation.

The direction is not dead, but its precondition is now explicit: **research-policy learning needs a
search space where the budget actually binds.** With regret ≈ 0 at the measured budgets, allocating
compute cannot change the answer. That is an argument for a harder problem, not for a better
controller — which makes it a reason to run the candidate-distance experiment below, not a reason to
keep tuning this one.

## Reproduction

```bash
bash .cache/recon/rerun_replay.sh            # three budgets, writes the three JSON reports
bash .cache/recon/test_gate.sh               # 64 tests: the gate, its fire tests, its consumer
bash .cache/recon/seedbook_probe.sh          # the weight trajectory
bash .cache/recon/decision_divergence.sh     # decisions vs outcomes, node by node
```

The gate's fire tests are in `tests/test_research_loop.py` and `tests/test_distill_policy.py`,
including `test_the_real_sept_16_replay_no_longer_passes_the_gate`, which replays the recorded
numbers and requires a refusal.

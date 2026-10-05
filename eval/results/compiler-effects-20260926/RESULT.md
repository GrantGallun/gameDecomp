# Compiler-effect experiment, September 26

The first predictor failed its promotion test. It was inexpensive to run, but
its assembly predictions and search ordering were worse than the controls. It
is an opt-in research module; the campaign does not use it.

An ordinary two-edit search did reproduce a byte-exact `MusAsk`. The unchanged
production route independently found the same source in 42 compiler calls,
about 14 seconds. For this case the operational gap is running an already
eligible route; it does not need the predictor. This is a known research match
with inherited header assistance, not a new source-independent capability.

## What was tested

`solver/compiler_effects.py` extracts source-edit context, removed locals, call
crossings, verified source-line locality and parent instruction context. A small
nearest-context model predicts changes in opcode/register operand counts, stack
slots/frame size and adjacent instruction signatures. This is a lossy compiler
state, not complete assembly or a proof of behavior. Names, historical winning
sources and future child outcomes are excluded from model inputs. Compiler and
flag domains must agree; fewer than two development TUs cause abstention.

The cohort froze 40 pending retained candidates and 439 existing edits before
child compiles. Sixteen functions from 14 translation units supplied 174 fresh
development transitions. The fitted model and rankings were sealed before
compiling 265 evaluation children from 24 functions in 16 other translation
units. Twenty proposals abstained; 245 received predictions. Existing campaign
roots and headers make this an exposed, header-assisted experiment, not a clean
source-independent holdout. The motivating audio, pointer merge and load-order
functions were excluded. No local_web_merge candidate occurred in this cohort;
that mechanism's transfer therefore remains unmeasured here.

## Measured result

| Evaluation metric | Existing generator order | Predicted order |
| --- | ---: | ---: |
| Exact functions, top 4 / top 8 | 0 / 0 | 0 / 0 |
| Score-improved functions, top 4 / top 8 | 10 / 10 | 10 / 10 |
| Gradient-improved functions, top 4 / top 8 | 7 / 7 | 6 / 7 |
| Calls to first score improvement, summed over 10 improvable functions | 15 | 20 |
| Calls to first gradient improvement, summed over 8 improvable functions | 23 | 30 |

No candidate anywhere in either full pool was exact. A better ordering of this
pool could not have produced an exact match. On the same 265 compiled evaluation
children, mean weighted assembly-state error was **19.375** for the prediction
policy versus **12.300** for predicting no compiler change. Among the 245
non-abstaining predictions it was 19.811 versus 12.158. This rejects this
representation/model as a useful ranking mechanism on the tested panel; it does
not establish that compiler effects are unlearnable.

The post-freeze pipeline took **66.13 seconds wall time** with three workers,
including 479 ordinary score calls (40 baselines + 439 children). Summed score
call time was 140.19 seconds. Feature extraction plus evaluation ranking cost
6.145 seconds, about 23.2 ms per candidate; fitting cost 0.038 seconds. Cheap
predictions did not translate into a matching speedup. Replay timings use the
full-pool measurements, not separate online races. Cohort enumeration, staging,
implementation and review are outside the 66.13-second pipeline measurement.

## Interpretation and next action

The predictor's immediate blocker is its representation of compiler effects.
This model compresses away which source value owns a register, live-range
interference, allocator priority and instruction dependencies. Averaging nearby
count changes does not reconstruct those relationships. The prospective result
also shows why more ranking work alone is insufficient: this one-edit pool has
no exact child to find.

A separately declared follow-up used the eight gradient-improved children as
actual parents for one further generation of ordinary edits, capped at 32 per
parent. Of 256 proposals, 250 compiled and 220 passed the frontend. Two variants
made **MusAsk exact**, and an additional ordinary compile independently confirmed
the first. Moving one statement raised its score from 84.129 to 85.903; widening
one local from `u16` to `s32` (or `u32`) then reached 100 with an exact object
certificate. The campaign ledger had no raw exact for it; the research ledger
already had one. This is a reproduced known, header-assisted match, not a new
capability claim or a predictor success. The follow-up took 29.36 seconds with
three workers, plus confirmation. All 256 parent edges were audited, including
six compile failures. See [the follow-up report](followup/RESULT.md).

That is exposed development work and does not revise the frozen result.

A final private canary started from the original retained `MusAsk` source, with
no winning source or child receipt supplied, and ran the unchanged production
`regalloc_search` defaults (budget 300, beam 3, depth 4). It reached the same
exact source in **42 compiler calls including the baseline, 13.67 seconds**.
Its path widened the local first and moved the statement second. All 42 scored
attempts and parent edges were accounted for. The original campaign checkpoint
already schedules this retained source for `regalloc_search`; that route had not
yet visited it. For this residual, the immediate blocker was unvisited eligible
work, not a missing edit generator. This does not establish that the scheduler
is optimal or that other residuals have the same cause. See
[the stock-route receipt](followup/route-report.json).

All 16 interrupted campaign attempts are now preserved, and the tested FP
emulator guard is installed at checkpoint 29293. Recovery retained all 998
object-exact/integrated and 24 function-exact pending nodes. A bounded continuation
of at most 415 deterministic jobs was launched; see `frontier/progress.json` for
actual progress, and `overflow/recovery/amendment.json` for the one-file amendment.
The rejected predictor remains outside production.

For compiler inference beyond this probe, the next representation should bind
source expressions to measured value/live-range graphs and predict constrained
graph edits. Trace-only reproduction of allocator choices is not evidence that
the child graph can already be predicted.

## Receipts and verification

- `manifest.json`: frozen sources/proposal identities and TU split.
- `code-pins.json`: 504 pinned model, generator, compiler and workspace inputs.
- `model.json`, `evaluation-rankings.json`: sealed pre-evaluation predictions.
- `report.json`: per-function outcomes, prediction errors and timing limits.
- `pipeline.json`: stage order, commands, exit codes and wall times.
- `audit.json`: all **479** scored sources and actual parent edges verified;
  no baseline score drift, no already-exact baseline and no new exact child.
- Native receipts: `/home/grant/decomp/experiments/compiler-effects-20260926/`.

Twelve predictor tests, eight harness tests and 70 differential tests passed
together. The neighboring generator/locality suite passed as well. No main KB,
production game source, model service or production ranking was changed by this
experiment. All experiment artifacts remain training-ineligible.

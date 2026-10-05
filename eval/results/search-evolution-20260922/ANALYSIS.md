# Learning from the rejected search policy — September 22, 2026

The original rejection combined a useful behavior with a specific failure. We
can preserve the useful behavior: a revised scheduler retains every known exact,
finds five exacts on the nine diagnostic cases instead of breadth's four, and
uses 152 compiler calls instead of 240. This is a freshly compiled **retrospective
development improvement**, not held-out transfer or a new RSI generation.

Four separate follow-up functions still produce no exacts for the revised policy,
breadth or beam. The original S0 remains active. No production TU, campaign KB,
model weights or default solver policy was promoted by this experiment.

## What worked

The expanded repair stream contains useful edits that compose. Four successful
traces need exactly two edits after the baseline. Following their first substantial
improvement immediately reaches the second edit and a compiler-certified exact.

| Function | Baseline → first repair → exact score | Successful repairs |
|---|---|---|
| Fvibup | 68.966 → 88.478 → 100 | Collapse unsigned conversion, rebase byte cursor |
| Fvibdown | 69.667 → 88.958 → 100 | Collapse unsigned conversion, rebase byte cursor |
| Fdistort | 55.160 → 68.615 → 100 | Advance cursor, change local type |
| loadMusicSequenceBank | 97.190 → 99.914 → 100 | Use compound field local, repair symbol scale |

All four paths are `root → root/0 → root/0/0`. Breadth delays the second repair
while visiting siblings. Greedy's speedup was useful evidence that repair
composition and timely follow-up matter. The exact certificate, rather than a
score of 100 alone, determines success.

The rejection gate also worked: it prevented one lost exact from being hidden
by a lower aggregate compiler cost. Complete histories, explicit parent edges
and saved generated sources made the failure diagnosable without reference
implementation bodies.

## What failed, and why

**Wobble was a scheduling failure, not an absent repair.** Its baseline score was
97.045. The first child, a compound field-local edit, improved that to 97.500.
Greedy spent the remaining budget pursuing that branch and never returned to
the root. It used 32 calls on only 13 distinct source hashes: 19 source visits
repeated a previously compiled source through another path. The exact candidate
was already available as root child 10, reached by breadth at call 12.

The winning sibling combines the field-local edit with removing a redundant
byte mask from the condition. The failed branch kept
`!(arg0->wobble_count & 0xFF)`; the exact sibling used `!arg0->wobble_count`.
The generated source and target compiler establish this result; it is not a
claim that removing masks is generally safe.

Repeated sources amplify wasted work, but do not explain the initial mistake:
the search treated a gain of 0.455 as sufficient to starve a useful sibling.
Global deduplication was not changed here. Parent-local streams preserve
deterministic replay; any compile cache must preserve context and lineage.

**The proposal grid skipped a useful middle range.** The original research
quartiles produced depth penalties 0.193 and 7.307. The first is too weak to
prevent the Wobble detour; the second delays useful descent on loadMusicSequenceBank.
For their motivating first moves, a penalty above 0.455 but below 2.724 can
discourage the former while preserving the latter. This is a local explanation,
not a universal guarantee about later branches or other functions.

**The development loop failed to use a known regression early enough.** The
earlier DREAM experiment had already exposed greedy's Wobble loss. Selection
still considered only the four research cases, chose greedy, and rediscovered
the loss at evaluation. The gate protected correctness but did not itself turn
the failure into a reusable selection constraint. Reporting only the rejection
would discard both its causal evidence and its successful traces.

## The bounded revision

`eval.search_evolution.propose` now offers at most four alternatives: greedy,
the two existing observed depth penalties, and their geometric midpoint. Original
research observations alone produce `sqrt(0.193 * 7.307) = 1.18754`, rounded to
six decimals. The policy prioritizes observed score minus penalty times depth;
it does not use function names or reference answers as features.

`select` accepts explicit `development-regression` wrappers and requires complete
replay plus retention of their known exacts. Their original evaluation partition
is preserved. They do not feed the proposer or model training. In this follow-up,
Wobble is the registered regression. The original nine cases are all excluded
from subsequent transfer measurement.

| Automatically proposed policy | Research exacts / calls | Wobble regression | Selection |
|---|---:|---|---|
| Parent breadth | 2/4 / 103 | Exact, 12 | Baseline |
| Greedy | 3/4 / 41 | Nonexact, 32 | Filtered |
| Depth 0.193 | 3/4 / 41 | Incomplete historical replay | Ineligible |
| Depth 7.307 | 2/4 / 70 | Exact, 12 | Eligible |
| Depth 1.18754 | 3/4 / 41 | Exact, 12 | Selected |

Selection froze before fresh compilation. The driver copies the completed
pilot's isolated solver engine and overlays only the revised selector and the
diagnostic driver. Repair generators, input sources, compiler recipes and object
targets stay comparable. Shared workspace changes cannot alter the frozen run.
The original generic experiment driver does not automatically load regression
wrappers; this diagnostic driver explicitly supplies the registered case.

## Fresh compiler results

Each scheduled arm has a 32-call ceiling, including its baseline. Compiles use
independent native WSL workspaces. Every distinct exact output is independently
recompiled with a frontend pass and object-section certificate.

| Diagnostic function | Breadth | Revised depth policy |
|---|---|---|
| Fvibup | Exact, 29 | Exact, 3 |
| Fvibdown | Exact, 29 | Exact, 3 |
| Fdistort | Exact, 10 | Exact, 3 |
| loadMusicSequenceBank | Nonexact, 32 | Exact, 3 |
| __MusIntProcessWobble | Exact, 12 | Exact, 12 |
| releaseMenuAssetHandles | Nonexact, 32 | Nonexact, 32 |
| allocTranslationOnlyFixedMatrix | Nonexact, 32 | Nonexact, 32 |
| updateRacePlayerLeanAngle | Nonexact, 32 | Nonexact, 32 |
| __MusIntProcessVibrato | Nonexact, 32 | Nonexact, 32 |
| **Total** | **4/9, 240 calls** | **5/9, 152 calls** |

That is one additional exact within this budget and 88 fewer compiler calls
(36.7%). It is not a new global inventory entry: all five exact functions were
known before this follow-up. Fvibup/Fvibdown are source-independent; Fdistort and
Wobble are header-assisted; loadMusicSequenceBank is project-header-assisted.
These assistance levels are retained in the receipts.

A fresh greedy negative control again misses Wobble at 32 calls and score 97.5.
The revised policy preserves all five previously known exacts across the original
research and comparison panels.

Post-run sensitivity replay on the fresh diagnostic histories gives the same
5/9 exacts and 152 calls at penalties 0.5, 1.0, 1.18754, 2.0 and 2.5, with complete
replays for all nine cases. At 3.0 and 7.307 it falls to 4/9 and 181 calls.
Penalty 0.25 lacks coverage for one requested continuation and cannot support
a complete panel comparison. This sensitivity analysis is retrospective; it did
not choose the already frozen policy and adds no independent compiler evidence.

## What the harder failures tell us

Four functions unused by the original completed pilot were tested separately.
They are still previously exposed, header-assisted SBK1 development functions,
not sealed holdouts. Neither parent nor candidate solves any of them in 32 calls;
beam also gets 0/4, using 121 total calls versus 128 per scheduled arm.

| Function | Baseline score | Breadth best | Revised best | Observed search behavior |
|---|---:|---:|---:|---|
| __osDequeueThread | 27.600 | 34.182 | 34.182 | Repeated sources in both arms; 19/21 unique sources respectively |
| osCreateViManager | 74.222 | 78.515 | 76.870 | Revision descends earlier and misses a better shallow result |
| updateRacePlayerAirborneLaunch | 94.822 | 94.869 | 94.869 | Both examine 31 distinct root children; little improvement |
| updateRacePlayerMode06TerrainFall | 90.451 | 91.067 | 91.067 | Both examine 31 distinct root children; little improvement |

All candidates in these scheduled follow-up arms compile; intake failure is not
the explanation. The last two cases suggest the examined first-step repairs offer
too little useful progress. They do **not** prove an exact is absent elsewhere
in the generator stream or behind a temporary score decrease. osCreateViManager
also shows that the fixed depth penalty is not universally preferable, even on
the weaker similarity metric. It passes no transfer gate here.

The next repair investigation should separate these failure classes:

1. Test bounded return-to-frontier behavior on the osCreateViManager detour while
   retaining Wobble and the four fast exacts.
2. Measure repeated-source savings with a compiler-context-bound cache that keeps
   each parent edge auditable, using __osDequeueThread as a motivating case.
3. Inspect the remaining target residuals on the two nearly flat root searches,
   then add a repair only with a test showing it fires on that residual and with
   fresh paired compiler results. Increasing the same search budget alone is not
   yet justified by these traces.

## Cost, verification and reproducibility

| Follow-up work | Compiler calls |
|---|---:|
| Nine diagnostic cases, breadth | 240 |
| Nine diagnostic cases, revised | 152 |
| Wobble greedy negative control | 32 |
| Four follow-up cases, breadth | 128 |
| Four follow-up cases, revised | 128 |
| Four follow-up cases, beam | 121 |
| Independent confirmations | 5 |
| **Total new calls** | **806** |

Runtime after setup was 259 seconds, within the 1,024-call and 20-minute bounds.
No model calls or training steps were used. The earlier completed pilot's 665
calls and aborted run's 134 actual compiles remain separate: 1,605 total calls
across those runs and this follow-up. The aborted run's 25 callback failures did
not launch compilers and are not counted as compiles.

The read-only audit validates all 806 source receipts, 775 explicit parent edges,
27 recorded worlds, frozen selection, source/certificate bindings, five independent
confirmations, beam comparison, development retention and the separate follow-up
gate. It reproduces selection using the frozen code. The original 665-call pilot
also passes its audit using its own unchanged code snapshot.

**106 focused tests pass on Windows and WSL**, including the new midpoint and
explicit-regression tests. Independent code review found no blocking issues.
The full repository suite was not run. All experiment records remain
training-ineligible. The additional diagnostic success is retained as evidence
and a reproducible policy artifact, without changing the original rejection or
claiming a recursive capability gain.

Artifacts: [frozen selection](diagnosis-v1/selection.json),
[diagnosis](diagnosis-v1/diagnosis.json), [complete report](diagnosis-v1/report.json),
[trace metrics and sensitivity](diagnosis-v1/metrics.json),
[receipt audit](diagnosis-v1/audit.json), [WSL tests](tests-diagnosis-wsl.txt),
[driver](diagnose.py), [audit script](audit_diagnosis.py).

The native code snapshot is
`/home/grant/decomp/experiments/search-evolution-20260922/code-diagnosis-v1`.
The private attempt database is `diagnosis-v1/attempts.sqlite` under the same
experiment directory. Run `audit_diagnosis.py` with the WSL Python environment
to reproduce the audit. `postmortem_metrics.py` regenerates descriptive metrics
without new compiler calls.

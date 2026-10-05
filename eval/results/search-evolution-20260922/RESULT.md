# Automatic search-policy development — September 22, 2026

This report records the original completed pilot and its unchanged rejection.
The subsequent [diagnosis and repair](ANALYSIS.md) recovers its useful speedups
without losing Wobble, and separately measures the limits of that revision.

The system now automatically proposes scheduling changes from compiler history,
selects a candidate by replay, freezes it, compares it with its parent and the
existing beam search, and rejects regressions. The first completed experiment
**kept S0**: the selected greedy candidate reached **1/5 exact matches**, versus
**2/5 for both its parent and beam search**. There was no production promotion,
model training, new inventory entry, or demonstrated recursive capability gain.

## What changed

`eval.search_evolution` reuses the existing live scheduler and replay engine.
Its deterministic proposer derives two bounded depth penalties from observed
positive score steps and also offers the existing greedy recipe. At most three
alternatives are considered. Selection requires complete replay, preserves the
incumbent exact set, then ranks exact count, score progress and compiler cost.
The policy receives observed scores, depth and expansion counts, not future
outcomes or source answers.

The paired capability gate requires at least one gained exact and no lost
exacts. It recomputes outcomes from source-bound certificates, checks compatible
environments and shared expansions, and rejects missing history or infrastructure
errors. Later rounds have a separate retention gate for previously earned
evaluation matches. The driver also forbids losing an existing beam-search match.
Efficiency improvements alone cannot advance a capability generation.

The bounded driver uses the existing generation manifests and budget ledger.
Candidates freeze before evaluation; successful sources receive independent
recompilation. A second round starts only after acceptance and uses the accepted
parent. This conditional second round is tested but **was not executed**, because
round one failed its gate. A stopped or interrupted run preserves partial files
and is not called complete; this standalone driver refuses to overwrite a run
rather than resuming an incomplete stage.

The proposal algorithm, panels, budgets and rules were developer-authored.
The numeric proposal values, candidate selection and rejection below were
machine-generated without an assistant editing the candidate during the run.
The selected recipe itself is an existing search algorithm. This is bounded
search-policy adaptation, not a learned researcher generating new repair code.

## Research and frozen selection

Four generated development drafts, 32 compiles per function/policy including the
baseline. Repair generators include the preceding representation improvements.
No reference implementation bodies were read.

| Policy | Exact research functions | Compiler calls |
|---|---:|---:|
| S0: breadth | 2/4 | 103 |
| Greedy | 3/4 | 41 |
| Automatically derived depth penalty 0.193 | 3/4 | 41 |
| Automatically derived depth penalty 7.307 | 2/4 | 70 |
| Total research cost | | **255** |

Greedy won the declared tie order. It closed `Fvibup`, `Fdistort` and
`loadMusicSequenceBank` in three compiles each. Breadth needed 29, 10 and more
than the 32-call ceiling respectively. Neither closed `releaseMenuAssetHandles`.
These outcomes selected a candidate; they did not establish transfer.

## Separate development comparison

Each arm starts from the same generated source in an independent native WSL
workspace and receives the same 32-call ceiling. Beam uses width 3 and depth 4.

| Function | S0 breadth | Candidate greedy | Existing beam |
|---|---|---|---|
| `Fvibdown` | Exact, 29 calls | Exact, 3 calls | Exact, 29 calls |
| `__MusIntProcessWobble` | Exact, 12 calls | Nonexact, 32 calls | Exact, 12 calls |
| `allocTranslationOnlyFixedMatrix` | Nonexact, 32 calls | Nonexact, 32 calls | Nonexact, 32 calls |
| `updateRacePlayerLeanAngle` | Nonexact, 32 calls | Nonexact, 32 calls | Nonexact, 32 calls |
| `__MusIntProcessVibrato` | Nonexact, 32 calls | Nonexact, 32 calls | Nonexact, 32 calls |
| Total | **2/5, 137 calls** | **1/5, 131 calls** | **2/5, 137 calls** |

The gate rejects S1 for losing Wobble, retaining S0. The 26-call vibrato saving
does not compensate for a lost exact. Five distinct exact outputs across research
and evaluation were independently recompiled and passed the frontend and object
certificate. The production match set was unchanged throughout the completed run.

These are previously exposed SBK1 development cases. They are function-disjoint
within the experiment, but **not sealed or family-disjoint**: Fvibup/Fvibdown
are siblings. No SBK2 or other sealed evaluation set was used, and all receipts
remain training-ineligible. The historical DREAM pilot had already observed a
greedy Wobble regression; this run remeasures it with the expanded generators,
not as a new scientific discovery.

## All costs, isolation and checks

The first run stopped after a shared workspace edit changed
`solver/call_arity_repair.py` from its frozen hash. Its 134 actual compile receipts
are retained as aborted. The scheduler also recorded 25 callback failures caused
by the freeze assertion; those did not launch compiles and are not counted as
compiler calls. No policy from this partial run was promoted or reused for
selection. The corrected driver propagates control stops immediately.

The completed run used an isolated 729-file code/input snapshot on the native WSL
filesystem. Concurrent workspace edits therefore could not change its code.

| Work | Compiler calls |
|---|---:|
| Aborted shared-workspace run | 134 |
| Completed research collection | 255 |
| Three-arm development comparison | 405 |
| Independent confirmations | 5 |
| **Completed run** | **665** |
| **All attempted runs** | **799** |

The completed run took approximately 211 seconds after setup. It made zero model
calls. The preregistered two-round ceilings were 1,280 research compiles and 1,120
evaluation/confirmation compiles, with a 30-minute runtime ceiling. Round two
consumed none of that allowance after the rejection.

**103 focused tests passed on Windows and WSL.** Coverage includes proposals,
incomplete replay, exact-set retention, contradictory shared observations,
source/certificate checks, infrastructure failure receipts, generation manifests
and shared budgets. Independent review found no remaining blocking issues after
the regression tests and fixes. The final read-only audit verifies all 665 source
bindings, 634 explicit parent edges, five confirmations, replayed selection,
beam losses and the decision to retain S0. The full repository suite was not run.

## What this establishes on the route to RSI

The executable laboratory gate now separates an appealing calibration result from
a change that earns continuation. The missing capability is still a policy or
repair intervention with repeatable transfer. A real next generation must earn
that gain and preserve prior matches; it then has to produce another accepted
change on a new panel. Improving the researcher itself remains untested.

The Wobble failure motivates future work on exploration that can retain promising
alternatives instead of following only the highest immediate score. Any revision
needs a new frozen experiment and regression coverage; the rejected panel is not
silently converted into model-training data.

Artifacts: [preregistration](pilot-v2/preregistration.json),
[automatic proposals](pilot-v2/r1-proposals.json),
[frozen selection](pilot-v2/r1-selection.json),
[decision](pilot-v2/r1-decision.json), [complete report](pilot-v2/report.json),
[audit](pilot-v2/audit.json), [aborted costs](pilot-v1/aborted.json),
[WSL tests](tests-wsl.txt).

The completed native code snapshot is
`/home/grant/decomp/experiments/search-evolution-20260922/code-v3`;
the private database is under `pilot-v2/attempts.sqlite` in the same experiment
directory. The snapshot and generation manifests retain the exact measured code.

# The cohort loop was bound by selection, not by the pipeline: +16

2026-09-17, round 4 of the recursive-improvement goal. One flag changed, nothing else.

## The diagnosis this tested

Round 3 measured object-exact per cold cohort and found it collapsing with shrinking batches:

| run | per-stratum | cohort | exact |
|---|---:|---:|---:|
| v9 | 1 | 12 | 2 |
| v10 | 1 | 5 | 0 |
| v11 | 1 | 4 | 0 |

Batch sizes shrinking *while 1,611 functions had still never been attempted* says the selector, not the
pool, is the constraint: `per_stratum_per_batch = 1` runs out of candidates in the strata
`fresh_run_v1.py` samples. So the test was to widen it and change nothing else.

## Result

| run | per-stratum | batch 2 cohort | exact |
|---|---:|---:|---:|
| v12 | **3** | 12 | **4** |
| v13 | **5** | 18 | **6** |

against 0 of 9 at per-stratum 1. Same pipeline, same entry point, same **zero model calls**.

## Reconcile: all 16 reproduced at 100.0

`eval/cohort_reconcile.py --ledger-glob 'failure-coverage-fresh-paired-v1[23]-*.json'` — 16 nodes
claimed object-exact and **all 16 reproduced** through the ordinary path, so none was taken on a
ledger's word:

`__osSiCreateAccessQueue`, `__osSiGetAccess`, `_collectPVoices`, `alEnvmixerNew`, `alSynAllocFX`,
`createRaceSetupOpponentFocus`, `func_80057E10`, `handleRaceTypeSelectMenuSelection`,
`initRaceSetupSavePanelIcons`, `initRaceSplitscreenSelectOption3Frame`, `initThrownPickupModel`,
`openStartupReplaySaveMessageFlow`, `returnToRaceTypeSelectMenu`, `startEndingSlashHandshakeLoop`,
`updateRaceFlowFrame`, `waitEndingLindaPhase31`

| | before | after |
|---|---:|---:|
| byte-exact | 245 | **261** |
| SOLVED | 179 | **195** |
| attempts logged | 51,144 | **51,160** |

## What this says about the remaining work

The pipeline's per-function yield on fresh ground was never the binding constraint; the size of the
cohort it is allowed to take is. The knob is monotone in the two points measured (0/9, 4/12, 6/18), so
the next round pushes it further — more per stratum, more versions, reconcile each — rather than
inventing machinery. That is grinding only if the yield flattens, and it has not.

## The thing worth naming, for the third time

Nothing about these 16 was hard. They were produced, verified by the object comparison, written to a
ledger, and then **not counted**. The reconciliation step is what turned them into matches, and it is
now a one-command step in the loop.

## Reproduce

```bash
PYTHONPATH=. python3 eval/experiments/campaign-gap-audit/fresh_run_v1.py \
    --version 12 --model-calls 0 --per-stratum 3 --max-work-items 120
python3 eval/cohort_reconcile.py --ledger-glob 'failure-coverage-fresh-paired-v12-*.json' \
    --out eval/results/cohort-reconcile-v12
```

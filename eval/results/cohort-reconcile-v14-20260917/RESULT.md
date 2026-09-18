# Round 5: per-stratum 8 yields 12 of 48, +12 verified

The cohort knob is monotone and round 5 pushed it again. One version, zero model calls.

## Yield by selection width

| run | per-stratum | cohort | exact | rate |
|---|---:|---:|---:|---:|
| v10 + v11 | 1 | 9 | 0 | 0% |
| v12 | 3 | 24 | 4 | 17% |
| v13 | 5 | 36 | 6 | 17% |
| **v14** | **8** | **48** | **12** | **25%** |

Both of v14's batches are in: batch 1 5/24, batch 2 7/24.

## Reconcile: all 12 reproduced at 100.0

`drawRaceTypeSelectArrowPrompt`, `initRaceIntroFlyoverShortPanFinal`, `initRacePlayers`,
`initRaceUiItemStealTrailEffect`, `spawnRaceUiAltBurstTextParticle`,
`updateControllerPakFileDeleteFileListUi`, `updateControllerPakReplaySaveMessageFirstPageFadeIn`,
`updateEndingCreditsTheEndTextFadeIn`, `updateEndingCreditsTransitionSnowflakeIconForwardSpin`,
`updateRaceIntroFlyoverLongPanHold`, `waitEndingJamPhase13`, `waitEndingNancyPhase36`

| | round 4 end | now |
|---|---:|---:|
| byte-exact | 261 | **273** |
| SOLVED | 195 | **207** |
| attempts logged | 51,160 | **51,172** |

## What the curve says

Total across rounds 4–5 of the same mechanism: **+28 matches from 141 cold functions at zero model
calls**, and the rate is *rising* with cohort size rather than flattening (17% → 25%). That is the
opposite of the saturation round 3 diagnosed, and it confirms the wall was only ever the selector.

v15 at the same setting is still running and will be reconciled next; the marginal cost is wall time,
not new machinery.

## Reproduce

```bash
PYTHONPATH=. python3 eval/experiments/campaign-gap-audit/fresh_run_v1.py \
    --version 14 --model-calls 0 --per-stratum 8 --max-work-items 200
python3 eval/cohort_reconcile.py --ledger-glob 'failure-coverage-fresh-paired-v14-*.json' \
    --out eval/results/cohort-reconcile-v14-20260917
```

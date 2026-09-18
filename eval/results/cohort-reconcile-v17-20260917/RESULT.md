# v17 at per-stratum 12: 12 of 56, ratchet to 299 / 233

The v16/v17 job finished. Its own reconcile, over the v15+v16+v17 glob:

| | |
|---|---|
| nodes claiming object_exact | 26 |
| already counted (correctly skipped) | 14 |
| **REPRODUCED-EXACT** | **12** |

`drawMainMenuTitleCursor`, `initControllerPakFileDeleteMainOptions`, `initRaceItemProjectileTrailEffect`,
`initRacePlayerSnowSpray`, `updateEndingJamPhase3DPrep`, `updateLaunchRampCourseObjectExit`,
`updateRaceSetupCornerPrompts`, `updateRaceUiPrizePayoutShowRankPrize`, `updateSpiralCourseObjectLaunch`,
`waitEndingLindaPhase38`, `waitEndingNancyPhase29`, `waitEndingTommyPhase0F`

| | session start | now |
|---|---:|---:|
| byte-exact | 216 | **299** |
| SOLVED | 150 | **233** |
| attempts logged | 51,095 | **51,198** |

## The curve is complete

| per-stratum | cohort | exact | rate |
|---:|---:|---:|---:|
| 1 | 9 | 0 | 0% |
| 3 | 24 | 4 | 17% |
| 5 | 36 | 6 | 17% |
| 8 | 48 | 12 | 25% |
| **12** | **56** (v17) | **12** | **21%** |

Absolute yield keeps growing with width; the rate sits around 17-25% and does not collapse. So the lever
is not exhausted — it is bounded by how many functions a run may take, which is wall time.

## What v16 shows about my own process error

`v16-batch-1` ended `paused_budget` with no summary: my `pkill` did land, or the run hit its timeout. So
v16 contributed partial nodes only. It cost nothing verified — the nodes it had written were counted, and
v17 covered the same width immediately after — but it is the second time this session that my scheduling
interfered with a run. The single-flight rule is now recorded in `FRONTEND_GOAL.md` for that reason.

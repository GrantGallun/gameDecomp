# Admission re-run: the never-compiled population against the current pipeline

Run 2026-09-16 22:59 against `/home/grant/decomp/kb-sbk1.sqlite`.

## Headline

| | |
|---|---|
| population (already attempted, never compiled, libultra excluded) | **72** |
| now compiles | **39** |
| — of which compiled as written by the model | 25 |
| — of which compiled only after the C89/target-linkage rewrite | **11** |
| now byte-exact | **1** |
| still does not compile | 33 |
| refusals | 0 |

Models: `gpt-oss:20b`. Draws per function: 1. Route: `reshape`. Declarations: OFF.

`eval/trajectory_factory.py:504` calls `generate()` with no `prefill`; this run passes `pipeline.PREFILL` on every draw, which `solver/llm.py:112-125` measures as 9/9 -> 0/9 refusals on functions that refuse every draw.

## Newly byte-exact

- `initRaceCameraFixedPositionFollow`

## The C89 / target-linkage hole, measured

These functions the model *did* write buildable C for. The pipeline was handing the compiler C99 (`inline`) and a linkage IDO discards (`static` with no caller), so the object came back with either `Syntax Error` at the opening brace or `Compiled object has no text symbols` -- and the admission triage booked both as separate failure kinds. They are one bug with two symptoms.

- `approachRaceIntroFlyoverOrbitRadius`
- `copyPackedMatrixTranslation`
- `dispatchRacePlayerMode30Attack`
- `drawMenuFillRectangle`
- `insertHuffmanQueueNode`
- `removeHuffmanQueueNode`
- `resolveAssetTableRelativePointer`
- `setPackedMatrixTranslation`
- `tryQueueRacePlayerHitCueSpinout`
- `updateControllerInputState`
- `updateRelocatableHeapNextFreeAddress`

## Newly compiling (not yet exact)

These are the functions the admission bucket was hiding: they were never a representation problem, only an un-retried one.

- `approachRaceIntroFlyoverOrbitRadius`
- `copyPackedMatrixTranslation`
- `decrementRaceChallengeTimeLimit`
- `dispatchRacePlayerAirborneMode`
- `dispatchRacePlayerMode30Attack`
- `drawMenuFillRectangle`
- `func_80060544`
- `getRaceCourseNextSurface`
- `initControllerSubsystem`
- `initFixedTransform`
- `initRaceCameraChase`
- `initRaceCameraCourseStart`
- `initRaceCameraFixedPositionFollow`
- `initRaceCameraIntroPan`
- `initRaceCameraMenuPreview`
- `initRaceCameraPositionTransition`
- `initRaceCameraRotationTransition`
- `initRaceCameraStaticFollow`
- `insertHuffmanQueueNode`
- `multiplyFixedMatrix3s`
- `pushRaceCourseSurfaceBoundaryWithVelocity`
- `removeHuffmanQueueNode`
- `renderSnowboardTrailEffect`
- `resetViewport`
- `resolveAssetTableRelativePointer`
- `resumeGameTask`
- `runRenderCallbacks`
- `scaleFixedMatrix3sByQuarter`
- `setPackedMatrixTranslation`
- `setRaceCameraModeForced`
- `setViewportOverlayColor`
- `suspendGameTask`
- `tryQueueRacePlayerHitCueSpinout`
- `updateCallbackTasks`
- `updateCallbackTasksWithMinPriority`
- `updateControllerInputState`
- `updateRaceCameras`
- `updateRelocatableHeapNextFreeAddress`
- `updateRemainingCallbackTasks`

## Per-function

| function | compiled | exact | best | seconds | outcomes |
|---|---|---|---|---|---|
| `approachRaceIntroFlyoverOrbitRadius` | True | False | 76.47 | 39.69 | raw:scored, c89:scored=76.5 |
| `clampRacePlayerVectorXZHalfSpeed` | False | False | 0.00 | 206.7 | raw:scored, c89:scored |
| `copyPackedMatrixTranslation` | True | False | 62.88 | 57.61 | raw:scored, c89:scored=62.9 |
| `decrementRaceChallengeTimeLimit` | True | False | 96.06 | 27.8 | raw:scored=96.1 |
| `dispatchRacePlayerAirborneMode` | True | False | 92.50 | 10.73 | raw:scored=92.5 |
| `dispatchRacePlayerMode07CourseObject` | False | False | 0.00 | 17.14 | raw:scored |
| `dispatchRacePlayerMode30Attack` | True | False | 100.00 | 19.55 | raw:scored, c89:scored=100.0 |
| `dmaReadRom` | False | False | 0.00 | 282.99 | raw:scored, c89:scored, chain:c89+undeclared:declare:scored |
| `drawMainMenuTitleOptions` | False | False | 0.00 | 207.88 | raw:scored |
| `drawMenuAssetRegion` | False | False | 0.00 | 470.63 | raw:scored, c89:scored |
| `drawMenuFillRectangle` | True | False | 0.00 | 117.91 | raw:scored, c89:scored=0.0 |
| `drawMenuSpriteClipped` | False | False | 0.00 | 781.63 | :generate-failed |
| `drawMenuSpriteCrossfade` | False | False | 0.00 | 188.31 | :generate-failed |
| `drawMenuSpriteSubrect` | False | False | 0.00 | 187.05 | :generate-failed |
| `drawRaceIntroFlyoverActor` | False | False | 0.00 | 188.51 | :generate-failed |
| `drawRaceTypeSelectOption0Frame` | False | False | 0.00 | 187.37 | :generate-failed |
| `drawScaledAssetTableSprite` | False | False | 0.00 | 188.72 | :generate-failed |
| `func_80057E90` | False | False | 0.00 | 187.12 | :generate-failed |
| `func_80060544` | True | False | 71.19 | 118.23 | raw:scored=71.2 |
| `func_800615BC` | False | False | 0.00 | 311.09 | :generate-failed |
| `getAssetTableImagePaletteAndSize` | False | False | 0.00 | 69.2 | raw:scored, c89:scored |
| `getRaceCourseNextSurface` | True | False | 33.12 | 22.8 | raw:scored=33.1 |
| `getRaceCourseSurfaceType` | False | False | 0.00 | 56.14 | raw:scored, c89:scored |
| `initControllerSubsystem` | True | False | 78.92 | 22.87 | raw:scored, c89:scored, chain:c89+undeclared:declare:scored=78.9 |
| `initFixedTransform` | True | False | 49.20 | 5.52 | raw:scored=49.2 |
| `initMainMenuSceneModelParts` | False | False | 0.00 | 60.2 | raw:scored |
| `initMenuTilemapSprite` | False | False | 0.00 | 6.21 | raw:scored |
| `initRaceCameraChase` | True | False | 90.00 | 11.63 | raw:scored=90.0 |
| `initRaceCameraCourseStart` | True | False | 26.77 | 5.56 | raw:scored=26.8 |
| `initRaceCameraFixedPositionFollow` | True | True | 100.00 | 6.82 | raw:scored=100.0 |
| `initRaceCameraIntroPan` | True | False | 51.04 | 7.06 | raw:scored=51.0 |
| `initRaceCameraMenuPreview` | True | False | 11.76 | 7.38 | raw:scored=11.8 |
| `initRaceCameraPositionTransition` | True | False | 0.00 | 5.0 | raw:scored=0.0 |
| `initRaceCameraReplayPosition` | False | False | 0.00 | 8.12 | raw:scored |
| `initRaceCameraRotationTransition` | True | False | 27.50 | 2.13 | raw:scored=27.5 |
| `initRaceCameraStaticFollow` | True | False | 78.59 | 6.65 | raw:scored=78.6 |
| `initRacePlayer` | False | False | 0.00 | 310.46 | :generate-failed |
| `initRaceSceneFlow` | False | False | 0.00 | 308.89 | :generate-failed |
| `insertHuffmanQueueNode` | True | False | 62.64 | 14.53 | raw:scored, c89:scored=62.6 |
| `isPositionNearCurrentRaceViewportCamera` | False | False | 0.00 | 1.83 | raw:scored |
| `multiplyFixedMatrix3s` | True | False | 10.52 | 4.71 | raw:scored=10.5 |
| `noopFourArgs` | False | False | 0.00 | 0.4 | raw:scored |
| `noopThreeArgs` | False | False | 0.00 | 1.1 | raw:scored |
| `prepareRaceResultsFlow` | False | False | 0.00 | 309.98 | :generate-failed |
| `pushRaceCourseSurfaceBoundaryWithVelocity` | True | False | 13.63 | 151.79 | raw:scored=13.6 |
| `removeHuffmanQueueNode` | True | False | 29.50 | 11.07 | raw:scored, c89:scored=29.5 |
| `renderRaceItemBreakParticle` | False | False | 0.00 | 311.69 | :generate-failed |
| `renderSnowboardTrailEffect` | True | False | 56.56 | 16.71 | raw:scored=56.6 |
| `resetViewport` | True | False | 71.36 | 4.9 | raw:scored=71.4 |
| `resolveAssetTableRelativePointer` | True | False | 20.00 | 1.62 | raw:scored, c89:scored=20.0 |
| `resolveRaceCourseSurfaceCollisionWithVelocity` | False | False | 0.00 | 0.05 | :generate-failed |
| `resumeGameTask` | True | False | 99.38 | 3.49 | raw:scored=99.4 |
| `runRenderCallbacks` | True | False | 11.43 | 2.9 | raw:scored=11.4 |
| `scaleFixedMatrix3sByQuarter` | True | False | 18.07 | 4.99 | raw:scored=18.1 |
| `setPackedMatrixTranslation` | True | False | 15.77 | 4.55 | raw:scored, c89:scored=15.8 |
| `setRaceCameraModeForced` | True | False | 42.10 | 3.17 | raw:scored=42.1 |
| `setViewportOverlayColor` | True | False | 62.86 | 3.86 | raw:scored=62.9 |
| `suspendGameTask` | True | False | 82.81 | 5.53 | raw:scored=82.8 |
| `transformVec3iByFixedMatrix` | False | False | 0.00 | 12.6 | raw:scored, c89:scored |
| `tryQueueRacePlayerHitCueSpinout` | True | False | 68.11 | 3.33 | raw:scored, c89:scored=68.1 |
| `updateCallbackTasks` | True | False | 46.21 | 1.63 | raw:scored=46.2 |
| `updateCallbackTasksWithMinPriority` | True | False | 72.83 | 3.24 | raw:scored, chain:raw+do_while:for_break:scored, chain:raw+do_while:for_break+undeclared:declare:scored=72.8 |
| `updateCharacterSelectLimitedCourseList` | False | False | 0.00 | 116.07 | raw:scored |
| `updateControllerInputState` | True | False | 25.19 | 13.02 | raw:scored, c89:scored=25.2 |
| `updateCourseSelectExtraCourseIconList` | False | False | 0.00 | 159.36 | raw:scored, chain:raw+do_while:for_break:scored, chain:raw+do_while:for_break+undeclared:declare:scored |
| `updateRaceCamera` | False | False | 0.00 | 10.21 | raw:scored |
| `updateRaceCameras` | True | False | 89.06 | 2.7 | raw:scored, chain:raw+do_while:for_break:scored=89.1 |
| `updateRaceHud` | False | False | 0.00 | 237.13 | raw:scored, c89:scored, chain:c89+undeclared:declare:scored, chain:c89+undeclared:declare+undeclared:declare:scored |
| `updateRacePlayerAirborneCruise` | False | False | 0.00 | 0.06 | :generate-failed |
| `updateRelocatableHeapNextFreeAddress` | True | False | 90.44 | 11.78 | raw:scored, c89:scored=90.4 |
| `updateRemainingCallbackTasks` | True | False | 55.89 | 2.55 | raw:scored=55.9 |
| `validateControllerPakSaveData` | False | False | 0.00 | 10.81 | raw:scored, c89:scored, chain:c89+undeclared:declare:scored |

## Status / errors

## Independent verification and caveats (added after the run)

**The match reproduces, and its tier is SOLVED.** The receipt above is the harness's own claim, so the
stored source was recompiled through the oracle in a fresh call: `compiled=True exact=True
score=100.0`, byte certificate `exact: True`. Tier is SOLVED, not header-assisted -- its only include
is `common.h`, and it declares its own struct with binary-derived offset labels:

    /* 0x801124A0 is a global structure with a function pointer at offset 0x2C.
       The structure type is defined here. */
    typedef struct { u32 unknown0; /* 0x00 */ u32 unknown4; /* 0x04 */ ... } ...

`D_801124A0` was the most frequent undefined symbol in the class measured separately by
`eval/admission_symbols.py` (10 occurrences), so the model named the address-only data symbol, wrote
the layout from offsets, and the oracle agreed.

**The C89 / target-linkage rewrite is responsible for 11 of the 39.** That is the round-1 fix's
measured payoff on this population, and it is the number to quote for it rather than the raw 39.

**`still does not compile: 33` overstates the failures.** 14 of the 72 failed generation on every draw,
and every one of those is a socket timeout at the 300-second bound. `CAMPAIGN_PERFORMANCE.md` records
that a context switch forces an Ollama model reload which can exceed that on a large prompt, so some
of the 14 are probably slow rather than impossible. They deserve a retry at a longer bound before
being counted as failures -- at `--timeout 600` earlier in the session, functions that had timed out
at 180 came back and scored.

**The next input for the near-miss closer is in this table.** Several newly-compiling functions sit at
high scores and are exactly what `eval/close_nearmiss.py` searches: `resumeGameTask` 99.38,
`updateRelocatableHeapNextFreeAddress` 90.44, `updateRaceCameras` 89.06, `suspendGameTask` 82.81.


# Plateau exploration: September 10 development experiment

Both strategies used the same source, toolchain, maximum depth (6), and
candidate compile cap (48). Baseline/final verification were outside that cap.
The second cohort required at least four available rewrites before selection.
It overlaps the first cohort; these are not twelve independent functions.

| Cohort | Function | Original score | Existing beam | Plateau mode | Compiles old/new | New max depth | Frontend |
|---|---|---:|---:|---:|---:|---:|---|
| v1 | updateControllerPakFileDeleteErrorPrompt | 99.722 | 99.722 | 99.722 | 1/1 | 1 | pass |
| v1 | initThrownTrailImpactProjectile | 99.667 | 99.667 | 99.667 | 0/0 | 0 | pass |
| v1 | func_800643B4 | 99.583 | 99.583 | 99.583 | 0/0 | 0 | pass |
| v1 | updateCourseTextureMarkers | 99.214 | 99.214 | 99.214 | 48/0 | 0 | blocked |
| v1 | drawScoreAttackChallengeLabels | 99.13 | 99.13 | 99.13 | 0/0 | 0 | pass |
| v1 | initFallingActionProjectile | 99.127 | 99.127 | 99.127 | 48/48 | 6 | pass |
| v2 | updateCourseTextureMarkers | 99.214 | 99.214 | 99.214 | 48/0 | 0 | blocked |
| v2 | initFallingActionProjectile | 99.127 | 99.127 | 99.127 | 48/48 | 6 | pass |
| v2 | updateRaceCoursePropModels | 97.889 | 98.0 | 97.889 | 48/0 | 0 | blocked |
| v2 | updateRacePlayerMode53AerialTrick | 97.557 | 97.557 | 97.557 | 48/15 | 2 | pass |
| v2 | updateRacePlayerMode31AerialTrick | 97.48 | 97.48 | 97.48 | 48/15 | 2 | pass |
| v2 | updateRacePlayerMode44AerialTrick | 97.48 | 97.48 | 97.48 | 48/15 | 2 | pass |

10 distinct stalled functions; 0 exact results in the new mode.

Interpretation: exploration and deeper compositions activate, but this pilot
does not demonstrate a matching-rate improvement. High similarity is not proof
of a local maximum: missing rewrites and frontend type errors are separate blockers.
Frontend-blocked cases are excluded from claims about scheduler quality; the
existing beam can spend its budget on those while the new mode declines them.

86 focused tests passed. A synthetic equal-budget regression reaches an exact
solution through two worse intermediate candidates that the existing beam misses.
That synthetic result validates scheduling behavior, not game-function recovery.

The live campaign was not modified. No model calls, reference function bodies,
source integration, held-out claims, semantic improvement claims, or whole-ROM
verification are involved. Project headers and saved bootstrap drafts are assisted
development context. Each arm has private SQLite attempt/parent receipts and a
freshly compiled champion object and verifier result. See each report.json for logs.

# Held-out 50 — results

Pre-registration: `PREREGISTRATION.md`. Analysis: `analyse.py`. Raw: `treatment.jsonl`, `control.jsonl`, attempts in
`/home/grant/decomp/runs/heldout50-20260929/`. 50 of 50 paired, 0 harness errors.

| | result |
|---|---|
| treatment wins / losses / ties (best `site_edits.gradient`) | 7 / 2 / 41 |
| one-sided sign test | p = 0.090 |
| **decision** | **not significant → continue** |
| exact | treatment 0, control 0 |
| mean best-score difference | −0.03 |
| compiles | treatment 1,424, control 1,232 (+16%) |

Wins: Flength, clampRacePlayerVectorXZSpeed, initRaceCourseSurfaceData, osMotorInit, renderPickupShardParticle,
updateMainMenuSettings, updateRacePlayerMode28TerrainFallWithItemEffect. Losses: updateRacePlayerMode15AerialTrick,
validateControllerPakSave.

## Reading

Predictions: ties dominated (held), wins ≈ 12:4 (fewer: 7:2), a non-significant result plausible (held), exact
0–2 (0). Across all 50 functions **one** shape edit was compiled (a temp_copyback). Six of the seven wins came from
ordinary typed edits, credited to the budget/interleaving changes rather than the new generators. The generators are
correct where they fire, but they fire on about 2% of unsolved functions. The maturity gap is **coverage**.

## Status of this frame

**Sealed.** Nobody reads, tunes on or develops against these 50 functions. After the next coverage round the same
two arms are re-run on them, with the control unchanged (this morning's search). Development continues on the
remaining 418 functions of the pool (`make_frame.py` pool minus this frame).

## Frame correction (2026-09-29, found after the tests)

The "unsolved" pool counted only functions with no exact attempt row. The campaign state also marks functions
`integrated` or `function_exact_pending_integration` without such a row. The sealed 50 contain 2 of those (1 integrated,
1 pending) and the development 50 contain 4 (2 and 2). They start at a perfect state in both arms, so they can only tie;
no reported direction or significance changes. Future frames exclude them by reading node status
(`eval.campaign_state.read`).

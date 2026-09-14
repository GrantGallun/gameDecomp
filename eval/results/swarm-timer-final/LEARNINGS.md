# Selected compiler guidance and timer context

Read the production function workspace's `AGENTS.md` and selected `DECOMPILATION_LEARNINGS.md` sections. Existing frozen candidate already compiled, so experimental sources remained separate from `base.c` and production sources.

- **When to use these vs. the permuter:** trust the production object certificate, not an OSS score including debug/symbol collateral. Every experiment used `workspace.score`, with unchanged production IDO and build helper.
- **IDO codegen: register allocation nudges:** source lifetimes can change register choice while preserving arithmetic. The target carries the absolute difference and successive quotients in one register, suggesting reuse of an existing scalar rather than declaration permutation.
- **AGENTS matching phases / duplicated variables:** remove decompiler-created intermediate locals before artificial register nudges. Direct remainder stores followed by in-place division were sufficient for exactness.

The public declaration in `include/game/race/ui/race_hud.h` is `s32 calculateRaceTimerDelta(RaceTimer *arg0, RaceTimer *arg1, RaceTimer *arg2)`, adjacent to elapsed/challenge race timer HUD routines. The binary takes two timer records, computes an absolute difference using signed minutes/seconds and the masked fraction, stores a decomposed result in the third record, and returns the ordering flag. It makes no function calls. Candidate signature has the same three pointer arguments and 32-bit signed return. Only generated candidate C, public declarations, and target assembly were inspected; no reference implementation was used.

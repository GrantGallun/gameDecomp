# Timer swarm result

`calculateRaceTimerDelta`: **94.324 → 100.0, exact allocated object sections and relocation expressions**. Project C frontend passes. This is a candidate artifact, not source integration or whole-ROM verification.

The target reuses one integer for the absolute difference and successive quotients. The previous candidate created independent `diff`, `quotient`, `remainder`, `sec`, and `min` locals. Its arithmetic was correct, but those distinct live ranges steered IDO to different registers and schedules.

Two composable source operators solved it:

1. Reuse the dead input local in both absolute-difference branches: 94.324 → 99.191.
2. Store each remainder directly into its destination field, then divide the same local in place: 99.191 → 100.0.

The generic generator reproduced the exact result at isolated attempt 31972. The cleaned candidate's independent replay is attempt 31965 in `replay.json`. All **175/175 semantic cases pass**: original six plus the Cartesian product of 13 explicit timer values covering equal inputs, fraction masking, minute/second rollover, and signed byte/halfword boundaries. Reachable instruction and feasible branch-edge coverage are both 100% under the current coverage model. These finite cases do not constitute a universal semantic proof.

Experiments v1–v5 contain 32 builds including repeated baselines and exact reproductions. Two early generated candidates failed compilation; the already-coalesced-local case causing those failures is now excluded and regression-tested. Eight focused generator tests pass.

The reusable implementation is `solver/timer_alternatives.py`; it operates within the named function, preserves strings/comments/member identifiers during renaming, declines escaping locals, loops, nested declarations, unsigned locals, and unsafe conditions, and requires normal compile/replay acceptance. No compiler flags, build guards, or production sources were modified.

Artifacts: `calculateRaceTimerDelta.c`, `cases.json`, `replay.json`; bounded experiment scripts are under `eval/experiments/code-shape-search/swarm_timer*.py`.

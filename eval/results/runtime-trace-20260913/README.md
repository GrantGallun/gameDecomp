# Recorded whole-call replay → repair prompts (2026-09-13)

Mechanism map: `PIPELINE_MAP.md` → "Recorded whole-call replay". Main tree only;
**not deployed** to the frozen campaign.

## What was recorded

Pinned portable Project64 4.x (interpreter core), attract-mode play, no input.
The ROM (`input.z64`) is git-ignored. The `portable/` emulator copies (about 7 MB each) are
run inputs; do not commit them.

| run | result |
|---|---|
| `coverage-1` | 240 s; 54.9M calls, 552 call targets, 393 named functions reached (244 compiling-unmatched, 32 compile-blocked). Title demo race is reached. |
| `record-<fn>-1` | 6 whole calls each for loadRaceMotionJointAnimationFrame, initRaceUiBurstTextParticle, updateRacePlayerMode16AerialTrick, getRacePlayerRankingProgress. getRaceCourseSurfaceHeight and drawRacePlayerModel were not recorded (batch stopped on an existing-directory error). |

## Replay validity (`eval.trace_replay_pilot`)

Candidate = the campaign's currently selected C, compiled in an isolated
workspace. Control = the original assembly replayed as the candidate (must pass).
Known-wrong = the original with ONE executed store shifted (must fail wherever
that store ran).

| function | candidate | control | known-wrong |
|---|---|---|---|
| loadRaceMotionJointAnimationFrame | 6 passed | 6 passed | 6 failed |
| getRacePlayerRankingProgress | 6 passed | 6 passed | 6 failed |
| updateRacePlayerMode16AerialTrick | 6 passed | 6 passed | 1 failed, 5 passed* |
| initRaceUiBurstTextParticle (98.725) | **6 failed** | 6 passed | 6 failed |

All 24 recordings passed the gate; none was unusable. *The mutated store
(`sh t8,0x302(s0)` → `0x304`, instruction 13) executed in only one of the six
recorded calls. The other five pass because the store never ran, not because
replay cannot see it; the 1/6 failure is the recording where it executed.

The initRaceUiBurstTextParticle failure is a real bug, not a replay artefact.
`getAssetTableImageAndPalette(asset, index, void **image, void **palette)` receives
image = a0+0x38+4n and palette = a0+0x28+4n in the game. The candidate's
generated struct interleaves image_n/palette_n from 0x28, so it passes
0x28+8n / 0x2c+8n.

## Does the model get it and act on it? (`repair_pair.py`)

Same source, seed, budget, model (campaign's resident gpt-oss:20b on 11435 at
32k, no second residency), isolated workspace and private KB copy per arm. The
only difference is `--recordings`. n=1 per configuration: these are
mechanism checks, not a capability measurement.

| run | harness state | outcome |
|---|---|---|
| recorded (v1) | first-divergence text; search stops on a childless depth | Model diagnosed it from the recording ("image pointer should be the higher offset, palette the lower"). Its C edit's old span occurred twice (both branches), so it was rejected. 2 of 6 calls used, no child. |
| baseline (v1) | same, no recordings | 6 calls, 1 compiling child, no improvement. |
| recorded-v2 | + every differing call listed, ambiguous-anchor lines named, `--exhaust-budget` | Applied the swap with disambiguated spans: 98.725 → **98.824**. Still 6/6 recorded failures (a swap is only partially right). The next 7 proposals repeated that swap or undid it, and were rejected as duplicates. |
| baseline-v2 | same harness, no recordings | 8 calls, 5 invalid, one child at 98.529, no improvement. |
| recorded-v3 | + candidate member names measured by the target compiler (`a0+0x38 (&arg0->image2)`) | Edited the struct declaration instead: 98.725 → **98.922**. Still 6/6 recorded failures. After that it repeated itself (duplicates and no-ops) until the stall restart. |
| recorded-v4 | v3 harness, 16 calls, seed 20260914 | 6 compiling children, best **98.922** (the same struct edit), 6/6 recorded failures. It oscillated between swap variants. |
| baseline-v4 | same, no recordings | 11 compiling children, reached 98.824 (the swap) several times, and one regression to 85.895. Search did not improve its best. |

Reading:

- The mechanism works end to end. Recorded evidence reaches the model as the
  primary counterexample, and its productive edits target exactly the
  divergent arguments.
- At 6–8 calls, only the recorded arm improved.
- At 16 calls the baseline finds the same swap from the instruction diff.
  `addiu a2,s0,0x38` states these offsets too. For this function the
  recording mostly makes the fix faster to find rather than adding new
  information. That is expected for a pure layout bug. Recordings should matter
  more where byte diffs are confounded: wrong values, wrong conditions, wrong
  callee results.
- No arm became exact or behaviourally correct. The 20b model never completes
  the layout (palettes 0x28..0x34, then images 0x38..0x44), and it spends much of
  its budget re-proposing already-evaluated sources.

### After the harness fixes (recorded-v5)

Three changes went in: partial credit (`distance` in the semantic key), duplicate
feedback that names the earlier outcome, and the zero-model recorded member repair.
The same function was rerun with 16 calls and seed 20260914.

- The member repair ran before any model call. It turned 48 offset constraints
  into a consistent 8-member rename with no declines.
  - It compiled at **99.510** (from 98.725).
  - It **passes 6/6 recordings and 64/64 synthetic cases**. Before, it failed
    6/6 and 64/64.
  - The remaining residual is **7 register-allocation faults**, with 0 offset,
    layout or structural faults: code shape only.
- The model's 16 calls did not improve on that. They produced temporaries and
  register hypotheses that failed to compile, and escaped edits.
  - The duplicate message now carries the earlier result ("this exact C is the
    depth-1 child … score 99.510 … Do not propose it again").

So for this function, the recording and a mechanical repair did what the model
couldn't. What's left is the allocator problem, which recordings can't help with.

## Files

- `record_batch.py`, `coverage-job.json`, `record-job-1.json`: recording jobs.
- `replay-<fn>-v2`, `replay-initRaceUiBurstTextParticle-v3`: pilot outputs (`replay.json` holds reports,
  controls, the known-wrong mutation, and the prompt text).
- `repair_pair.py`, `repair-pair.status`, `repair-initRaceUiBurstTextParticle-<arm>[-vN]/`:
  paired repairs (`run.log`, `receipt.json`, `receipt.best.c`).
- `detail-<fn>.json`: dashboard `/api/function` snapshots used to bind the campaign's selected source.

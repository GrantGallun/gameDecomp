# Hosted agent vs local model on the 81-200 instruction tier

Written **before** the run. The question is narrow and it is not "is Codex
better": it is whether the wall at ~80 instructions is *model capability* or
*pipeline shape*. The campaign's answer to date is 4 exact of 433 functions in
this tier with `gpt-oss:20b`, so if capability is the binding constraint a much
stronger model should show it here.

## Arms

Identical kernel (`solver/modelrepair.py` through `eval.agentrepair`), identical
prompts, identical budgets, identical parents. Only the provider differs.

| | control | treatment |
|---|---|---|
| provider | `solver.modelrepair:OllamaProvider` | `solver.codexprovider:provider` |
| model | `gpt-oss:20b` (local) | `gpt-5.3-codex-spark` (hosted) |

Budget per function per arm: `--draws 1 --depth 4 --beam 2 --max-calls 8
--seed 20260911 --timeout 600`. No `--structured-output`: the CLI accepts only
the strict schema subset and rejects the kernel's proposal schema, so neither
arm gets a schema and the prompt carries the shape for both.

## Known protocol differences, recorded not smoothed over

These favour neither arm cleanly and must appear in the writeup:

1. **No seed or temperature on the hosted arm.** The CLI exposes neither. The
   control arm is seeded. Treatment results are therefore one unseeded draw per
   call and are not reproducible except from cache.
2. **No assistant prefill on the hosted arm**; the prefill text is appended to
   the user prompt instead (`prefill_mode` in every receipt).
3. **Different inference budgets.** `num_predict` is ignored by the CLI, and the
   hosted model spends reasoning tokens the local one does not. This is a
   comparison of two configured systems, not a controlled per-token comparison.

## Selection rule (applied before any run)

From `functions` in `kb-sbk1.sqlite`, using metadata and prior-attempt outcomes
only -- no target source read:

1. `insn_count` between 81 and 200.
2. At least one attempt that compiled and is not exact: the arms need a shared
   starting candidate.
3. Not in any frozen held-out split (62 names across `eval/sets/`).
4. **No exact attempt anywhere in the KB for that function.** An exact row means
   the answer is already in the database, whether solved or copied from the
   reference decomp; repairing one measures retrieval.
5. **Parent strategy is not `authorized-target-history-recovery`.** Those
   candidates descend from reference material, so a match from one is not a
   capability result. This removed 20 of 35 otherwise-eligible functions.

That leaves 15. The six below span the parent-score range; the highest, lowest
and four spread between were taken, and `updateRaceTypeSelectCursor` was
excluded because it was used for the plumbing smoke.

| function | insns | parent attempt | parent score | parent strategy |
|---|---|---|---|---|
| initControllerPakRaceRecordSaveFlow | 115 | 23545 | 99.912 | semantic-stress-audit |
| updateCourseSelectCourseDescription | 126 | 16006 | 99.640 | faultsearch-d1 |
| updateRacePlayerMode37AerialTrick | 146 | 23283 | 96.149 | m2c-semantic-seed |
| serviceRumbleMotorRequest | 93 | 28110 | 95.489 | m2c-project-header-intake |
| alLoadParam | 117 | 29675 | 91.897 | agentrepair-type-constraints |
| packFixedTransformMatrix | 104 | 24142 | 88.462 | differential-debugger-causal-repair |

## Success criterion

**Primary: `exact=true` from the object verifier.** Nothing else counts as a
match. The weighted progress score is a diagnostic, and a score that rises
without reaching exact is the outcome this tier has produced for months.

Secondary, reported but not the claim: best score change, compiling children,
invalid proposals, calls used, wall time, tokens.

## Contamination check

The reference decomp is the answer key and lives in the WSL filesystem; the
hosted agent runs on the Windows side in an empty temporary directory with user
config ignored. Every command it executes is audited (`tool_call_count`).

**Any trial with a nonzero tool-call count is reported as contaminated and
excluded from the numerator**, whatever it scored.

## What this cannot establish

Six functions, one draw per call. A zero here does not prove the tier is
unreachable, and one match would not prove the tier is solved -- it would
license a bigger preregistered run on the remaining nine eligible functions.
Results are development evidence on DEV-eligible functions, not a held-out
benchmark, and every candidate is header-assisted as the whole pipeline is.

## Database

Writes go to `~/decomp/kb-sbk1-codexab-20260911.sqlite`, a copy taken
2026-09-11, so the authoritative KB and the paused campaign are untouched.

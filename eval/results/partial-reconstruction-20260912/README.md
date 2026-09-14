# Partial reconstruction pilot, 2026-09-12

Implementation and CLI: [PARTIAL_RECONSTRUCTION.md](../../../PARTIAL_RECONSTRUCTION.md).

Final regression suite: **2281 passed in 48.82s**, `full-tests-final.log`.
Focused region/controller/branch-context suite: **58 passed in 12.06s**.
Tests cover native C89 skeleton/branch compilation, wrong predicates bypassing
markers, coincidental dummy returns, shared tails, path regression, stale source
and target bindings, unavailable semantics, durable rejected proposals, private
SQLite lineage, and unchanged differential verdicts.

`selection.json` records large failed drafts selected from campaign history.
The real pilot uses `drawCharacterSelectCourseRecordsPopup`, attempt36166,
624 target instructions and 56 CFG blocks. Its original draft fails compiler
and frontend checks; its skeleton passes both. This is a structural result,
not recovered behavior or an exact match.

Experiments are isolated; each uses a complete private history and native WSL
workspace under `/home/grant/decomp/partial-popup*-20260912/`. No candidate or
history is imported into the live campaign. Subsequent trials copy the previous
private snapshot rather than copying the busy canonical database again.

- `popup`: original initialization declined when the failed draft had no
  normalized target artifact. This exposed and motivated skeleton bootstrapping.
- `popup-v2`: successful compiler/frontend skeleton bootstrap. Two local model
  requests (proposal3449/3450) were rejected for inconsistent child marker IDs.
  Raw responses and sampling receipts: `model-trial.json` and private SQLite DB.
- `popup-v3`: stricter statement-only replacement prompt, explicit child-ID
  instructions and bounded original failed-draft context. Its `state.json`
  records every final call outcome; private DB retains raw provider responses.
  Both model requests (3451/3452) exhausted their 6000-token budget with empty
  final content; the provider's thinking fallback is not valid proposal JSON.
  Both were rejected. Receipts: `popup-v3-model-trial.json`. Across all four
  model calls, no model-generated replacement was accepted.
- `guard-smoke.json`: separately authored structural proposal based on the
  target's entry `gRaceSplitscreenMode` load and conditional branch. It assigns
  the entry to block0, leaves block1 and blocks2-4 as distinct unfinished arms,
  and leaves blocks5-55 as an unfinished shared tail. This is explicitly not a
  model-generated repair or proof that an execution is correct.
  Applying this saved proposal succeeded: popup-v3/version-0001.json,
  attempt44862, compiler and frontend both passed, current version1. Three
  holes account for all55 remaining blocks. Status is still
  `compiling_partial_unvalidated`, complete=false.

The existing differential environment rejects this function's
`gGameSaveDataBuffer+0x196c0` write as exceeding its synthetic symbol stride.
Therefore the pilot is **unvalidated** regardless of compiling structure.
No holes, full differential verification, compiler/frontend, and exact-object
checks remain necessary for completion; no reduced gate is introduced.

## Follow-up: linked-global seed replay fixed

The original failure came from an unconditional64KiB check on serialized case
seeds, despite existing large-region support for linker-bound symbols. The check
now applies only to synthetic symbols. Large linked seeds still pass the existing
8MiB resource, 32-bit overflow and synthetic/reserved-memory collision checks.
Alias storage remains keyed by address. No broad stride increase or dense memory
allocation was needed.

`replay-memory.py` copies the old private function workspace and runs the corrected
panel without altering old pins, history or game sources. `memory-replay.json`
records success:64 test cases,64 raw failures on the unfinished guard, and64 cases
executing markers. This resolves the initialization blocker, not decompilation.
The initial exploration has4 returned runs and5 invalid-heap-handle memory faults;
those noncompleted inputs and remaining coverage gaps remain explicit debt.
Focused differential/memory tests:80 passed. Full suite:2286 passed in52.93s.
Full regression output is in
`memory-fix-tests.log`. The active frozen campaign is unchanged.

Model requests use this machine's verified Windows Ollama on the WSL gateway,
fixed32k context, medium effort, gpt-oss:20b, and the active campaign's shared
GPU lock. The first endpoint escalation was rejected pending destination
verification; Windows adapter/listener/process checks established it was local,
and the same request was subsequently approved.

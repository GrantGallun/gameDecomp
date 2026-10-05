# Clean failure histogram and header-conflict repair — September 22, 2026

Fixed a common intake failure introduced by header selection. On the same 200 assembly-only starting drafts, **100 functions have fewer frontend errors, ten additional functions compile under IDO, and five additional functions pass both IDO and the frontend. No new byte-exact matches.**

## What the earlier work established

Read the September 14 failure census, September 16 DeepSeek session review, September 21 intake reports, histogram/loop notes, progress audit, and contamination findings. The latest useful baseline is `../intake-20260921/wide-intake-clean.json`, not the earlier reference-seeded measurements. Those earlier passes addressed real failures but were often evaluated on drafts carrying reference-source names or layouts.

Recovered every clean frame's exact final source through its logged SHA256, then reran the real frontend. The histogram reproduced exactly: declaration conflicts in 144 states, pointer/member errors in 133. **All 144 conflict-bearing states include a conflicting declaration of the target function itself.** The earlier suggestion that unreconciled generated stubs explained the dominant bucket was not the root cause of this measurement.

## Root cause and change

`solver.compile_recovery.header_variant` always considered the target function's declaring header. A fresh assembly draft may define `void f(void *arg0)` while that header declares `void f(Actor *actor)`. Adding the header solely for `f` creates a conflict; ordinary declaration reconciliation correctly leaves the function definition intact.

The fix skips selecting a header solely for a differently spelled target signature **when all types in the draft's signature are already available**. It retains explicit includes and independently required callee, global, and type headers. Unresolved aliases keep the old recovery path, including scalar, enum, and callback typedefs. Complex/unparsed signatures also keep the old path. No candidate signature, frontend rule, object certificate, or promotion condition is changed.

Independent review found the alias edge cases in the first version. Each was reproduced with a failing test before correction. The final conservative guard produced exactly the same header candidate source and report on all 200 measured action inputs (`review-adjustment.json`); no measured result depends on the discarded alias implementation.

## Paired compiler result

| Outcome | Before | After |
|---|---:|---:|
| IDO compiles | 16 | 26 |
| IDO and frontend pass | 12 | 17 |
| Byte-exact objects | 2 | 2 |
| States with declaration conflicts | 144 | 44 |
| States with pointer/member errors | 133 | 133 |
| Total frontend errors | 4,931 | 4,830 |

The five new IDO-plus-frontend states are `__MusIntProcessWobble`, `updateRacePlayerLeanAngle`, `drawRaceSplitscreenSelectOption0Frame`, `drawShopMenuSelectedModePanel`, and `drawMainMenuModeSelectIcons`. Another state, `guMtxL2F`, becomes frontend-clean but still does not compile under IDO; it is excluded from the joint acceptance count.

All 200 baseline final-source hashes reproduced the saved September 21 receipt. No acceptance losses, no state with more frontend errors, and both previous exact objects retained. The existing `eval.quality_ratchet` passes. Class membership can change as diagnostics change; class totals are not repair distance.

This is **header-assisted development data**, starting from assembly-only drafts. It is not a new held-out evaluation or a binary-only solving claim. Both arms exclude the reference-source declaration recovery action. No model calls, reference game function bodies, training, live campaign amendment, or match promotion were used.

The paired run took 538 seconds and made **1,050 unique logged compiler attempts**. Arms reuse an identical source's verdict within the same function/workspace; reused verdicts are not additional compiles. Native builds and the private attempt database are at `/home/grant/decomp/experiments/clean-conflicts-20260922/`. The production KB was not written.

## Verification and remaining failures

- Final focused WSL tests: **20 passed**, including nine new regression cases.
- Full project `pytest tests -q --tb=short`: **4,118 passed, 15 failed, 41 errors, 8 skipped, 1 xfailed, 2 xpassed**. The exact same 56 failing test/phase reports reproduce with the pre-task header implementation. See `tests-before-existing-failures.json` and `tests-after-existing-failures.json`, with full tracebacks in the matching `.log` files. Existing failures include placeholder-stage expectations, campaign recovery, member-access expectations, benchmark contention, Project64, posttraining dependencies, and Windows WSL-launch tests running inside Linux.
- Unscoped `pytest -q` additionally stops during collection on ten `external/snowboardkids-decomp/tools/test_*.py` imports and `tools/lora_serve/tests/test_lora_serve.py` (missing `torch`). These are outside this change.
- Fresh `python -m eval.status` before and after: **347 byte-exact / 1,074 attempted: 256 SOLVED, 11 header-assisted, 25 reference-type-assisted, 55 recovered; 95,853 production attempts**. Its test inventory prints zero and is not the test result above. Existing documented assistance limitations on SOLVED still apply.

The remaining largest class is pointer/member access on 133 states. The next useful experiment is to group those actual source/diagnostic pairs by why existing typed-access repairs decline, using these same frozen inputs. Removing the other 44 target conflicts indiscriminately would discard headers needed by other symbols; this patch deliberately leaves those for a coordinated repair.

## Receipts and reproduction

- `census.py`, `census.json`, `states/`: source-hash recovery and fresh original diagnostics.
- `replay.py`, `paired.json`, `replay/`: paired real-toolchain results and final C per arm.
- `header-before.py.txt`, `header-after.py.txt`: pre-task and initially measured implementations. The baseline preserves earlier agents' uncommitted edits; it is not Git HEAD.
- `check_revision.py`, `review-adjustment.json`: final implementation equivalence at all 200 changed-action boundaries, with the final module digest.
- `summary.json`, `ratchet-before.json`, `ratchet-after.json`: aggregated results and existing ratchet inputs.

From the repository root in WSL, using `/home/grant/decomp/sbk1/.venv/bin/python`:

```sh
python eval/results/clean-conflicts-20260922/census.py
python eval/results/clean-conflicts-20260922/replay.py
python eval/results/clean-conflicts-20260922/summarize.py
python -m eval.quality_ratchet eval/results/clean-conflicts-20260922/ratchet-before.json eval/results/clean-conflicts-20260922/ratchet-after.json
python -m pytest tests/test_intake_self_header.py tests/test_header_alias_recovery.py tests/test_project_headers.py -q
```

The replay writes its output files; preserve this receipt directory before rerunning. The fix is in the main working tree and has not been deployed into a frozen campaign.

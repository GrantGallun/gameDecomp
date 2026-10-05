# Clean intake: returned pointers, global cursor loads, and nested casts

September 22, 2026. Continued from the exact saved candidates in `../clean-members-20260922/paired-final.json`, retaining the same 200 functions and source hashes. Fresh full frontend diagnostics reproduced the baseline before any changes. Project headers remain available; these are assembly-only starting drafts, not a header-free capability claim.

| Check on the same 200 candidates | Before | After |
|---|---:|---:|
| IDO compiles | 46 | **47** |
| Frontend passes | 43 | **45** |
| Both IDO and frontend | 42 | **44** |
| Object byte-exact | 4 | **4** |
| Frontend errors | 2,862 | **2,608** |

Twelve candidates improve, none has more frontend errors, and no candidate loses an IDO, frontend or exact pass. No new exact match this round. `allocTranslationOnlyFixedMatrix` newly compiles; `renderPickupShardParticle` already compiled and now passes the frontend. Member-reference errors fall from 1,011 to 752, across 77 to 72 affected functions. Integer/pointer conversions exposed by those repairs partly offset that reduction.

![Failure histogram: affected functions and individual errors](histogram.png)

## Root causes and changes

`solver.void_field_repair` counted `return temp_v0;` as another declaration of `temp_v0`. Its shadowing check consequently refused legitimate local pointers. Excluding the `return` keyword fixes that scanner error while retaining rejection of real shadow declarations. This affects `allocTranslationOnlyFixedMatrix` and `alAdpcmPull`; the former loses all 16 frontend errors and compiles at 95.865 similarity, while the latter remains blocked by other errors.

The larger remaining member-access cluster mixed memory accesses from unrelated bases at the same displacement. A rendering pointer copied from `gRegionAllocPtr` was being compared with unrelated byte/halfword loads at offsets zero and four. The existing binary dataflow already records `load(global)+offset`. The repair now uses that identity for a narrow source shape: one plain assignment from the global to a local `void *`, before the diagnosed use, with no local rebinding, address escape, or source shadowing. It requires a four-byte pointer load and retains the existing unanimous access-width check. No layout, field name, or extent is inferred.

Review found unsupported C declaration and parenthesized-lvalue forms that could evade the initial alias guards. The final rule conservatively declines declaration-shaped references, parenthesized aliases, and bodies containing local aggregate definitions. The tests include no-space, qualified and comma-separated declarations, anonymous struct/union shadows, for-init shadows, alias shadows, mutation and address escape. One previously proposed `__osViSwapContext` repair is withheld by the stricter rule; no compile or exact gain depended on it.

`solver.frontend_fixits.apply` treated nested diagnostic ranges as independent replacements. In `renderPickupShardParticle`, casting a call argument first shifted the enclosing call's end position. The outer cast then split `Transform3D` into `Tran))sform3D`, and the fixer rejected its own corrupted candidate. Casts now use insertion boundaries in the original source, supporting disjoint and nested expressions. Crossing ranges and conflicting target types for an identical range decline. No frontend checks were weakened.

These are corrections to the existing three-step intake route, not new pipeline stages. The same shared helpers are used by model-repair normalization. Every emitted candidate still passes through ordinary IDO compilation and object comparison.

## Receipts and validation

`paired-final.json` is authoritative for the final reviewed code; `paired.json` is the earlier replay before the alias guards were tightened. `states/*/before.c` and `states/*/final.c` are the final comparison sources. `summary.json`, `ratchet.json`, and `histogram.png` are generated from the completed final replay by `summarize.py`, which checks the denominator, source hashes, and per-candidate ratchet.

The preliminary `review-equivalence.json` correctly identifies the withheld `__osViSwapContext` proposal; it is not a claim of final equivalence. A fresh compiler replay follows that review adjustment. `void-declines.json` records the original ambiguity investigation, and `cast-overlap-before.txt` / `cast-overlap-after.txt` preserve the actual compiler diagnostics that exposed the nested-cast corruption. Archived pre-round implementations are included for reproducibility.

All compiler attempts, including failed exploratory proposals, are logged in private databases under `/home/grant/decomp/experiments/clean-residuals-20260922`. Native WSL build trees are isolated from the real game build. No production KB mutation, model call, held-out source-body input, game integration, or frozen campaign deployment was performed.

The final replay logs **212 attempts: 200 baselines, 11 member-repair candidates, and one cast-repair candidate**. `attempt-log-check.json` audits every exploratory and final compiler attempt; `code-verification.json` binds the final measured implementations to their current file hashes. All **60 focused tests pass in WSL**, including both model-repair consumers and the new positive/decline cases. Source ranges used by the cast-reproduction diagnostic probe are frontend-only checks, not unlogged IDO attempts.

The broad test run reports 4,189 passed, 15 failed, 41 errors, 8 skipped, 1 xfailed and 2 xpassed. Its 56 failing test/phase identities exactly match the existing baseline (`tests-comparison.json`). That broad run preceded the final conservative guard adjustment; the focused checks cover the final code. Independent review's reported guard gaps are resolved.

Remaining high-frequency failures include scalar globals used as arrays, pseudo-members on scalar or array types, incompatible pointers, and call-argument counts. Rendering candidates are closer, but several retain accesses through indexed texture pointers whose binary base correspondence is still unresolved. The repair leaves those widths unknown.

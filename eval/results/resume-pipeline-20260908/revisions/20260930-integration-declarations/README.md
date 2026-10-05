# Amendment: reconcile conflicting candidate declarations at integration

Status: **APPLIED 2026-09-30** (owner: "apply it", after reviewing the diff and the dry run); integrated 24 -> 31. Checkpoint
36025 -> 36026 (install; object_exact_or_integrated unchanged at 1,043). Follow-up to
`../20260929-integration-recertify/`.

## Problem

After the re-certification amendment, integration ran again but several function-exact candidates failed the
full-ROM build for one reason: the candidate's own `extern` disagrees with the declaration in the destination
translation unit. Examples: `extern s8 gFramebufferSwapHold;` against race_flow.c's `u8 gFramebufferSwapHold;`
(addEndingActorShadowRenderCallback, finishRaceStartTransition and others), and `extern char gFmt[];` against
`const char gFmt[16]`. The function is byte-exact with its own type, so the declaration has to yield while the
code keeps the candidate's type.

## Change (one file)

`eval/prepare_integration.py` = frozen copy + `_EXTERN`, `_conflicts`, `_param_types`, `_typed_uses`, and a branch
in `replace_function`:

- A candidate extern yields only when the destination file itself declares the same name with a different type,
  kind or parameter types. Equal declarations and names declared only in a header are kept as written.
- For a yielding object, every use in the body is rewritten to the candidate's type: scalar `(*(T *)&x)`,
  array `((T *)x)`. A local that shadows the name declines the rewrite and the extern is kept.
- A yielding function prototype is dropped and the body is left unchanged. Calls are never cast: a cast call
  can compile to `jalr` instead of `jal`.

Reviewed = main (sha256 `6151e8da...`); frozen before `41cfa0f5...`. Main differs from frozen by additions only.
No new imports.

## Evidence

- Tests: `tests/test_integration_declarations.py` (6, including the motivating fire test on the
  `gFramebufferSwapHold` shape and the declines). Staged run on a copied frozen tree: 58 passed with the recertify
  and frozen integration suites.
- Dry run (`dry_run.py`, copies of state and ledger, frozen code with only this file replaced;
  `dry-run-result.json`, 418 s): integrated 24 -> 27, pending 28 -> 25, union `rom_exact` in both sessions, no
  integrated member lost. New: closeRaceRecordSettingsFlow, drawRaceMotionAnimationDebugViewerMotionNumber,
  drawRaceSplitscreenSelectEntryFee.
- A first, broader version (rewrite every overlapping extern) broke two already-integrated members with a
  checksum mismatch in its dry run. That is why only real conflicts yield; the test
  `test_matching_or_header_only_declarations_keep_the_extern_as_written` pins it.
- Limit of the dry run: its driver stopped after two sessions (10 of 28 pending selected) because it broke on an
  empty `changed`. The live sessions below cover the rest.

## Applied (2026-09-30)

1. Both pause markers set; nothing in flight at checkpoint 36025.
2. `stage.py`: 3,326 unchanged pins verified, no drifted imports. `verify_stage.py`: 58 passed.
   `apply_amendment.py --apply`: checkpoint 36026, object_exact_or_integrated 1,043 unchanged, no rollback.
3. Pause markers removed. Integration-only sessions (launch command, `--max-work-items 0`, `--resume`) until the
   counts repeat: see "Sessions" below.

## Sessions

Five integration-only sessions (checkpoint, object_exact_or_integrated, integrated, pending integration):

| session | checkpoint | exact or integrated | integrated | pending |
|---|---|---|---|---|
| before | 36026 | 1,043 | 24 | 28 |
| 1 | 36037 | 1,046 | 27 | 25 |
| 2 | 36042 | 1,046 | 27 | 25 |
| 3 | 36047 | 1,050 | 31 | 21 |
| 4 | 36052 | 1,050 | 31 | 21 |
| 5 | 36057 | 1,050 | 31 | 21 |

**Integrated 24 -> 31, object_exact_or_integrated 1,043 -> 1,050.** Every run exited 0. Stopped after the counts
repeated twice. Session 3 added four more than the dry run reached, because the dry run's driver stopped early.
The 21 still pending are the long tail below.

## Not covered

- addEndingActorShadowRenderCallback: a callback function-pointer type conflict.
- `const` data passed to a `void *` parameter.
- "needs shared declaration integration" (drawEndingObjectSpriteDebugViewer, drawShopMenuMoneyPanel): the
  preparer deliberately supports only includes, declaration-only externs and one function.
- drawScoreAttackChallengeLabels, drawTitleScreenStartPrompt: build fails with no error line recorded.

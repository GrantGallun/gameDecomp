# Amendment: preparer names (integration preparer + relocation-name proposals)

Owner, 2026-10-01: item 2 of the ranked amendment list ("2 seems pretty good"). Machinery only: no node, receipt or
ledger row is changed by the install. **Status: APPLIED at checkpoint 37426** after the owner's October 1 instruction
to execute the maturity-map priorities. Both pauses and an empty in-flight queue were verified. Fresh copied-runtime
tests: 178 passed, 1 skipped; the existing intake failure was reproduced on the pristine frozen tree. All retained
nodes and the 1074 object-exact or integrated functions were preserved. See `stage.json`, `stage-test.json` and
`amendment.json`. The normal service was resumed with ten-item batches; live results are recorded separately in
`../../../maturity-execution-20261001/`.

## Why

- The integration preparer refused any candidate with text outside its one function except includes and externs. The
  2026-09-30 object-layer levers produced six function-exact rewrites that stopped there, and the latest live sweep
  recorded `preparation_blocked: shared declaration` on 2 of its 5 selected nodes.
- `solver/relocation_names.py` existed since 2026-09-14 and nothing in the pipeline called it.

## What changes (frozen files, `reviewed/` built by `build_reviewed.py`)

| file | change |
|---|---|
| `eval/prepare_integration.py` | carries candidate-local brace typedefs, object-like `#define` aliases and plain `const char NAME[N] = "literal";` objects into the destination TU. An identical block yields; a same-name different one declines. The frontend probe sees them. Function typedefs, pointer/array aliases, bare `struct X {...};`, function-like macros and other data still block. |
| `eval/operand_repair.py` | `relocation_names.variants` joins the proposals; `literal_names` is filtered out |
| `solver/relocation_names.py` | new generator `field_names`: the candidate reaches B+K through a struct field, the target names a separate global A there |
| `solver/repair_queue.py` | frozen file plus ONE hunk: the operand-repair digest covers `relocation_names.py` |

Frozen and main differ in the first three files by exactly these edits (`build_reviewed.py` asserts it by line-set
difference). Main's `repair_queue.py` carries unrelated edits and is not copied.

## Why `literal_names` is excluded

It swaps a real literal for `extern D_800E...`, which scores higher and cannot link: fadeInRaceGameplayViewports
went 99.52 -> 100 function-exact and then failed the whole-ROM build (`undefined reference`). Wiring it would pull
incumbents toward unlinkable forms.

## Evidence (trial DB and disposable ROM copies only)

- `../../hidden-object-20260930/linking_probe.jsonl`: the six function-exact cases' original sources through the
  whole-ROM gate with the certificate bypassed: six `rom_exact` alone, and **one batch of all six `rom_exact`** (two
  TUs, four sharing a file and its typedefs). func_8005C14C's own source aliases strings by `#define` + `extern`
  and cannot link.
- `../../hidden-object-20260930/relocation_names_worth.jsonl`: 36 variants over the 33 unsolved functions any
  generator reaches (of 222 with a named relocation mismatch): all compile, all improve, 3 function-exact, 0
  object-exact. Whole-ROM: updateRaceSetupPlayerCountPrompt (`field_names`) `rom_exact` alone; fadeInRaceGameplayViewports
  (`literal_names`, now excluded) `build_failed`.
- Tests: `tests/test_prepare_integration.py` (fire tests on the real residual shapes plus declines),
  `tests/test_relocation_names.py`, `tests/test_operand_repair_campaign.py`, `tests/test_integration_declarations.py`.

## Expected reach (prediction)

- Census over the campaign's pending nodes (2026-09-30): 12 of 1,087 newly clear the preparer's shape check (4
  `function_exact_pending_integration`, 8 `object_exact`); ~8 more pending nodes need bare-struct support (not in this
  amendment). Shape-ready is not integrated: the ROM gate decides each.
- Every frozen pin changes, so the integration sweep's evidence key changes and blocked nodes are retried. The operand
  digest changes, so pending nodes are revisited once (compiles; no extra model calls beyond normal operand visits).
- `field_names` fires on 1 of the 828 unsolved best candidates, so expect roughly one verified function from it.

## Procedure

Both pause markers, drained. Then:

1. `stage.py` (use `--dry` first)
2. `verify_stage.py`
3. `apply_amendment.py --apply`
4. resume the service

The campaign service was in `needs_repair` from 2026-09-30 21:06 (startup `model_digest` timeout while Ollama was
stopped); it must be resumed regardless.

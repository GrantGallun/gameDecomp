# Amendment: object layer (object routes + three object levers)

Owner, 2026-09-30: "if you think the campaign amendment is ready we can try that." Machinery only: no node, receipt
or ledger row is changed by the install.

## Why

The normalized asm diff is `.text` only and lossy. Over the 828 unsolved best candidates, 155 differ outside `.text`
(`eval/results/hidden-object-20260930/RESULTS.md`). Where that is the only fault, the diff is empty or spelling-only,
every diff-driven lane declines, and the node absorbs budget anyway. func_8005905C and func_8005C14C took about
1,060 campaign attempts with `.text` already matching.

## What changes (frozen files, `reviewed/` built by `build_reviewed.py`)

Each changed file is the frozen file plus only this layer's hunks, in the frozen file's own line endings. No whole
main file is copied: main's `residual.py` and `repair_queue.py` carry unrelated, unreviewed edits.

| file | change |
|---|---|
| `solver/workspace.py` | `Attempt.object` = `object_discrepancy.summarize` on every compiled attempt. Diagnostic only: an error here never fails the score. |
| `solver/residual.py` | `ResidualPacket.object` |
| `solver/repair_queue.py` | `object_route`. A `generator` route opens `operand_profile` at zero diff faults, and that visit goes first. A `certify` route returns no profile in the byte/environment lanes. `certificate_digest` and the operand digest cover the new modules. |
| `eval/operand_repair.py` | `rodata_symbol.address_variants` and `file_scope_objects.variants` on the incumbent's object |
| `solver/rodata_symbol.py` | the address-taken branch: target rodata label, or target symbol name when `.text` is identical |
| `solver/object_discrepancy.py` | new: compare, classify, summarize |
| `solver/file_scope_objects.py` | new: unused file-scope objects when only the candidate has `.bss`/`.data` |

`repair_queue.py` is staged on the current frozen copy (sha `b3a3274e...`, the jump-tables-plateau reviewed build).

## Evidence (trial DB and disposable ROM copies only)

- `layer_run2.jsonl`: 22 `generator` routes; 32 `certify` routes, all already function_exact; 0 `unexplained`.
- 8 lever wins: 2 object exact (guMtxIdent, drawCharacterSelectCourseExitPreviewPanel), 6 function exact.
- `object_exact_integration_dry.out`: **guMtxIdent rom_exact, whole-ROM verified**.
- The other 7 wins stop at `prepare_integration`'s shared-declaration limit.
- `stage-test.json`: 170 passed on a copied frozen tree with this overlay. The one deselected test fails on the
  untouched frozen tree too.

## Expected reach (prediction)

- The census used the best candidate across BOTH ledgers. For 6 of the 7 non-guMtxIdent wins, that source is in the
  kb ledger and the campaign node holds a different, weaker source. The code alone therefore reaches guMtxIdent
  (campaign node at score 100, zero faults), plus whatever future campaign attempts produce these shapes.
- The six kb-ledger rewrites are re-verified, object- or function-exact sources. They belong to the ledger-import
  route (`../20260930-ledger-import/`), not to this code amendment.
- The certificate digest changes, so `recertify@` revisits score-100 and relocation-only pending nodes once. That is
  zero-model, one compile each, and it gives existing packets their route.

## Procedure

Both pause markers, drained. Then:

1. `stage.py`
2. `verify_stage.py`
3. `apply_amendment.py --apply` (its `fires_ok` reads the evidence above)
4. resume the service

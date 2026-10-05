# Three verified repairs and one reusable search operation

September 22, 2026. These are assistant-authored development experiments on the
already exposed 17-state panel. They are not a local-model training result, an
autonomous RSI generation, or a held-out generalization measurement. No reference
function body was supplied to the repair process.

| Function | Starting score | Verified result | Assistance |
|---|---:|---|---|
| `Fdistort` | 55.160 | Object-exact; frontend passed | Header-assisted |
| `osEPiRawWriteIo` | 91.474 | ROM-backed function-exact, schema 3; frontend passed | Binary-only candidate |
| `osEPiRawReadIo` | 79.150 | ROM-backed function-exact, schema 3; frontend passed | Binary-only candidate |

All three were recompiled in separate native WSL workspaces, then recompiled
again when recording normal inventory receipts **95854–95856**. Existing game
translation units, binary evidence, and inference rows were not changed.
Whole-ROM integration has not been performed.

## What was preventing progress

The saved training experiments improved procedural tool choices without showing
a certified game-solving gain. The later deterministic dev run had already
closed `__MusIntProcessWobble`, so the existing work was not wholly ineffective.
It left `Fdistort` at 81% even after a larger search.

The target assembly made the missing operation concrete. The draft stored a
sign-extended value in `u8`, discarding the extension, and delayed advancing its
input pointer until the return. Widening to `s32` repaired the first defect.
Changing `value = *p; ... return p + 1;` to
`value = *p++; ... return p;` repaired the remaining instruction order.

`solver/cursor_advance.py` now generates this second edit through the existing
`regalloc_mutations` registry. It composes with the existing local-type mutation;
no function name or answer is embedded in the generator.

## Paired executable comparison

Both arms used identical frozen source, compiler, oracle, beam 3, depth 4, and a
**40-compile ceiling including the baseline**. The control filtered out only the
new mutation family. The result reproduced after code review:

| Arm | Actual compiles | Result |
|---|---:|---|
| Existing search without cursor advance | 24 | Search stopped at 81%, nonexact |
| Same search with cursor advance | 10 | Object-exact |

Receipt: [paired-search.json](paired-search.json).
The new generator fires on 1 of the 17 frozen starting states. This demonstrates
an absent operation on a known development residual, not broad transfer.

The I/O pair needed a register polling temporary and literal volatile hardware
accesses, with addresses derived from the target instruction words. Volatile
alone did not help. The resulting scores remain 98.421 and 98.500 because of
symbolic-versus-literal relocation spelling; schema-3 certification independently
establishes that the relocated function bytes reproduce the ROM. This MMIO repair
is recorded as a review pattern, not enabled as an automatic generator.

`FrandPan` remains unresolved: the best tested candidate reached 88.421, versus
88.158 in the preceding search. It is not counted as a match.

## Inventory, logging, and limits

Fresh `python -m eval.status` moved the object-exact inventory **347 → 348**:
SOLVED 256 unchanged; header-assisted **11 → 12**; reference-type-assisted 25
unchanged; recovered 55 unchanged. See [status-before.md](status-before.md) and
[status-after.md](status-after.md).

The two I/O certificates are logged in the main attempts database but do not
increase that object-exact count. The status command's separate function-exact
display still scans the canonical build tree rather than isolated experiment
workspaces, so its displayed 12 also excludes these two new certificates. Their
durable evidence is in [inventory-receipts.json](inventory-receipts.json) and
the `*--inventory-verification.json` artifacts here. No status code was changed.

There were **123 compiler invocations**: 46 exploratory, two paired runs of
34 each, six independent re-verifications, and three inventory re-verifications.
The private database contains 120 attempts, including two compiler failures:
`/home/grant/decomp/experiments/residual-repair-20260922/attempts.sqlite`.
Three additional receipts are in the normal KB. No local-model inference,
training, weight downloads, or external model API calls were launched.

All experimental rows are marked training-ineligible. A review of the initial
paired harness caught repeated action labels being mistaken for unique node
identities. Only those private inferred lineage edges were retracted/rebuilt;
attempts and compiler verdicts were retained. See `lineage-correction.json`.
Ambiguous parents are now explicitly left unknown.

## Validation and reproduction

**97 focused tests passed** after review. New tests first failed on the missing
operation, then on two review findings (unbraced conditional returns and casts
that change pointer units). The final implementation rejects both. The reviewer
reported no remaining findings. Existing regalloc, branching, tool-boundary,
and residual-pattern tests pass; the complete repository suite was not rerun.

```powershell
python -m pytest tests/test_cursor_advance.py tests/test_regalloc_mutations.py tests/test_regalloc_search.py tests/test_bounded_search.py tests/test_tool_boundary.py tests/test_residual_patterns.py -q
```

From the repository root in WSL, using the SBK1 venv:

```bash
/home/grant/decomp/sbk1/.venv/bin/python eval/results/residual-repair-20260922/probe.py
/home/grant/decomp/sbk1/.venv/bin/python eval/results/residual-repair-20260922/probe.py --round2 osEPiRawWriteIo osEPiRawReadIo FrandPan
/home/grant/decomp/sbk1/.venv/bin/python eval/results/residual-repair-20260922/probe.py --round3 osEPiRawWriteIo osEPiRawReadIo FrandPan
/home/grant/decomp/sbk1/.venv/bin/python eval/results/residual-repair-20260922/verify_search.py
```

`record_results.py` explicitly appends freshly verified, tier-labelled results to
the main attempt inventory and skips already-recorded candidate hashes. The other
scripts write only the private experiment log and isolated candidate workspaces.

The next useful learning experiment should start with repairs whose executable
paths have been demonstrated on eligible training functions, then measure transfer
on separate functions. Another policy-training pass over actions that cannot close
the chosen residual would not address the bottleneck found here.

# Range-split diagnosis: drawControllerPakFileDeleteConfirmOptions

## Evidence and limits

- `alloc-census-20260924/census.json` records a medium function at 99.889 with `first: split` and two `split` classes (`M`, `M`). That establishes two diagnosed split-class mismatches and that the first diagnosed mismatch is split-related; it does not identify an instruction, register, variable, or live-range boundary.
- `frontier-20260924/fronts.json` independently records 90 instructions, two steps, two register steps, and zero operand or structural steps at 99.889. This supports an allocation-only residual classification, not a register-choice explanation.
- `operand-repair-20260925/RESULT.md` reports broader operand-search outcomes, while `frontier-run-20260926/RESULT.md` explicitly says this target's prior operand edits stayed flat at 99.889 and points to the first wrong split boundary. Neither receipt gives a target compiler trace.
- `restored-holes-20260925/analysis.json` has no entry for this function. A target-specific candidate listing, disassembly pair, compiler trace, and source-to-instruction attribution were not present in the checked evidence directories. The recent run's baseline/final ledgers carry only status and source hash for this target.

Therefore the current receipts do not establish which register decision differs or where either split begins/ends. Do not infer a graph or choose a variable from the function name. The next required artifact is one aligned target/candidate function trace: target disassembly and compiler-attributed candidate instructions with instruction addresses, registers, and source-line attribution (plus the exact candidate source revision/hash and compiler flags). It must expose both mismatching live-range intervals or enough instruction data to reconstruct them.

## One discriminating experiment (after obtaining that trace)

1. Freeze the current candidate source hash and reproduce the 99.889 baseline once through the ordinary scorer. Save candidate assembly, target/candidate instruction alignment, and the compiler's register/lifetime trace. Locate the earliest split mismatch and identify the candidate value whose live range crosses that boundary; record target and candidate register assignments and last-use points. If the trace cannot attribute a value/range, stop and request that trace detail rather than guessing.
2. Make one isolated source edit that ends only that value's lifetime immediately after its evidenced last use before the first mismatching boundary (for example, a narrow lexical scope or a short-lived temporary, only if semantics and the trace support it). Do not combine it with operand, type, declaration-order, or second-boundary edits.
3. **Prediction written before compiling:** if the first split is caused by this excess live range, the candidate's register assignment should match the target at that boundary; earlier aligned instructions should remain unchanged; score should rise above 99.889 while the second split remains. If the assignment does not change there, or unrelated earlier/later ranges move first, the lifetime hypothesis is falsified for this edit. This predicts a local compiler effect, not byte-exactness.
4. Compile and compare through the normal scorer. Retain the probe even on failure. Check that the source diff contains only the one lifetime edit, the first-boundary register outcome matches the prediction, both split residuals are recounted, and neighboring functions/global exact-match count do not regress. Accept an exact only through byte comparison and the project's ordinary match gate.

No compile or source mutation was performed while preparing this protocol.

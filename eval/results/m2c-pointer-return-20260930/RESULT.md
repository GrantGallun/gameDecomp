# Address-return alternatives through m2c

Implemented the approved bounded change in the existing draft path. It corrects a provisional integer-return hypothesis through a separate pointer-return redraft, preserving the ordinary draft and all compiler acceptance gates. **The measured alternatives do not improve object code.** This option remains off by default.

## What the evidence exposed

The binary context emitter uses `s32` when it cannot attach a typed return node. Our m2c provenance export shows the heap lock/unlock helpers returning a cast around a pointer-valued `EvalOnceExpr`, associated with the instruction that computes the block address. `Flength` similarly returns byte-address operands on both exits. This makes a pointer-return hypothesis worth testing without claiming to know the original source signature.

The motivating heap-helper object residual has an address in `v1` instead of the target's `v0`, plus a final `move v0,v1`. The initial hypothesis was that removing m2c's integer return cast might remove that move. **The native compiler falsified that hypothesis:** it emits the same object with the pointer return and no explicit integer cast.

## Implemented flow

`solver.m2c_pointer_return.propose` uses the existing CFG, excludes assembler preludes, follows a bounded acyclic address domain, and checks every reachable return after its delay slot. Address roots come from matching named relocations or pointer arguments in the provisional context. Arithmetic views and context argument types remain retractable hypotheses. Loads are treated as words rather than guessed loaded pointers.

Calls, likely branches, cycles, unsupported instructions, ambiguous return paths and missing return boundaries decline. Transfers require a represented delay slot; a target label that splits a slot declines. Conditional branches at the end of the function decline when their fallthrough is missing. These last two cases were found in independent review, reproduced as failing tests and fixed.

`solver.binary_type_draft.variants(repo, function, ws, pointer_returns=True)` keeps the ordinary candidates and appends at most two m2c syntax spellings using the changed provisional return. A caller's `fixed_return=True`, conflicting context declarations or another public declaration found after preprocessing prevent the alternative. Hypothesis reports cite the assembly hash and return/address instruction sites. No source C answer, game header, evidence row or inference row is consumed or written by this path. The code fingerprint includes the generator. Existing callers use the default `pointer_returns=False`; no active campaign was amended.

## Paired native trial

The twelve functions were reused from the previous frozen DEV selection, without filtering by the new outcomes. Before generation, the trial fixed a budget of one valid-syntax baseline and at most one valid-syntax pointer alternative per function: at most 24 compiles, no adaptive resampling.

| Trial outcome | Count |
| --- | ---: |
| Functions inspected | 12 |
| Ordinary compile attempts | 12 |
| Pointer-return alternatives | 4 |
| Total compile attempts, all logged | 16 |
| Compiler and frontend passing attempts | 14 |
| Alternatives with changed allocated object sections | 0 |
| Byte-exact matches | 0 |

The four alternatives are `releaseRelocatableHeapBlockMetadata`, `lockRelocatableHeapBlock`, `unlockRelocatableHeapBlock` and `Flength`. All four remove the integer return view in their C while producing equivalent allocated object sections to their respective ordinary drafts. The two large sprite bodies still fail ordinary compilation on the previously observed byte-address issues; this trial deliberately isolates the return-type treatment and does not combine the prior byte adapter.

This is a source-independent development trial, not a held-out evaluation, original-signature proof, ROM integration or global capability result. Binary `v0` may also hold an incidental address in a function whose original C return was void. That is another reason to keep the signature change as a hypothesis behind declaration and compiler gates.

## Verification and limits

Tests were written before implementation, including the motivating indexed address return and scalar/mixed/declaration decline cases. After both review fixes, the relevant WSL suite passed **57 tests**, covering the generator and existing binary context, draft, identity and byte-view behavior. The Windows run passed 50 tests with three dependency/platform skips. A broader campaign test, `test_clean_placeholder_child_keeps_binary_evidence`, expects two scored attempts but gets four because the current admission path also makes two context attempts. The same failure was reproduced after removing this change from the module in memory; the test already stubs the draft generator. That unrelated campaign test and its current admission code were left unchanged.

`verify.py` passed its audit of all 16 private attempt records, parent edges, source/target/candidate hashes and 14 object certificates, and checked object equivalence for all four alternatives. Native build artifacts are retained at `/home/grant/decomp/experiments/m2c-pointer-return-20260930-v1`, copied locally under `portable/`. Compiler-run code snapshots are hash-bound. After both review fixes, `generation_check.py` reproduced every retained compiler source across all twelve inputs and the same four pointer-return proposals, without additional compiles. Its receipt binds the final generator and test hashes; the audit confirmed those hashes. WSL launch failures temporarily delayed these final checks, but the successful runs completed them. No model was called, no production KB or real build-path function was changed, and no global status count was queried.

## Implication for further m2c work

Changing the return cast is not the byte-matching lever for these cases. The more relevant m2c information is its expression identity and materialization state: which machine value becomes an emitted `EvalOnceExpr` temporary, where it was defined, and whether it could retain a source-like cursor/update expression instead of a split temporary. The existing register-repair machinery can consume such hints. This trial does not implement that next materialization adapter or assert that it will solve the residual.

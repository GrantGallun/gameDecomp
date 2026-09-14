# Callback allocation investigation

The retained candidate remains **98.511**, nonexact, with **76/76 semantic cases passing** in a fresh normal search. This round obtained compiler-stage evidence explaining the residual; it did not obtain a new byte-match improvement.

## What the compiler actually does

The isolated production-compiler diagnostic run retains the frontend and optimizer intermediates, then dumps code generation. Its emitted assembly matches the nondiagnostic assembly. In `../callback-allocation-backend-v1/ugen-dump.log`, the allocation sequence includes:

```text
emit_rab: zsh xr24 0 xr9
emit_rab: zlhu xr3 0 xr9
emit_rri: zmul xr25 xr3 4
emit_rab: zlw xr8 0 xr25
```

The count reload is already assigned register 3 (`v1`); the task pointer is already assigned register 8 (`t0`). The assembler subsequently forwards the count store/reload into the mask visible in final assembly. The allocation difference therefore predates that final mask instruction. The prologue narrowing operations use code-generator temporaries instead, so not all differing registers belong to the same allocation mechanism.

Inlining the index removes its allocation, but gives `v1` to the predecessor pointer and `a3` to the new task: the opposite of the target. Volatile diagnostic reads keep the index in `v1` and prevent store forwarding; they do not provide the desired source-level solution. These diagnostics are not admitted as equivalent replacement candidates.

Combining index inlining with constructing the task pointer before the predecessor fixes the persistent pointer assignments. This alternative scores **98.156**, below the champion, but its residual is simpler: a consistent one-slot shift in temporary-register selection with matching instruction order. It independently passes **76/76 semantic cases**, with zero inconclusive cases (`../callback-allocation-selector-v3/replay.json`). It is preserved in `../callback-allocation-selector-v3/00.c` for targeted investigation rather than replacing the champion. Eight subsequent cast/mask/parameter combinations did not improve it; their receipts are in v4.

Exact optimizer save scores remain unknown. The diagnostic option that prints them triggers an assertion in the installed compiler recompilation's floating-point formatting wrapper. The existing local allocator model contains an assumed cost numerator and cannot substitute for these missing measurements. See the backend report and captured logs for the precise boundary between observed allocation and inferred causes.

## A simpler verified residual despite a lower score

On the 98.156 alternative, `(u16)(s16)type` produces **97.979**, but now the entire function after the opening block matches the target. The candidate independently passes **76/76 semantic cases** (`../callback-allocation-backend-v2/closest-replay.json`). Its diff is confined to the first 13 normalized listing lines, including equivalent jump-table labels. It remains nonexact and does not replace the 98.511 score champion.

The actual code-generation trace shows two narrowing operations: the ABI normalization uses `t6`, and the explicit narrowing uses `t7`, advancing the temporary sequence so the selector uses `t8`. The assembler eliminates the second redundant mask, leaving the desired downstream register sequence but the wrong opening instructions. This isolates a late elimination/scheduling question rather than a function-wide allocation mystery. Eight additional prologue compositions did not reach exactness; a repeated-normalization version also passed all 76 cases.

The final two stage-derived tests separated the normalized local, selector evaluation, and parameter assignment. A u16 local scored 96.986 and a u32 local 95.390; neither resolved the opening block. In total the three branches compiled 122 source experiments (including controls and diagnostic source variants), in addition to the 36 normal-search evaluations and separate compiler-stage verification.

## Automatic search evidence

The normal search compiled 36 candidates, producing 26 distinct interpreter inputs, of which 25 differed from the parent. Twenty-eight candidates passed all 76 semantic cases; none improved the champion. This is a quality plateau, not simply repeated identical compiler output.

Search receipts now record per-wave compilation counts, semantic-clean counts, distinct interpreter inputs, nonparent inputs, and champion improvements, plus per-candidate assembly hashes. The reported expansion budget now reflects the configured value. These are measurements; no unvalidated automatic broadening policy was added.

Validation: 70 pilot/frontier/transition tests and 3 selector-generator tests passed. The normal search used zero model tokens. Compiler settings and production source were not changed.

## Artifacts

- `../callback-allocation-backend-v1/`: compiler commands, intermediates, stage dumps, and diagnostic failure evidence.
- `../callback-allocation-backend-v2/REPORT.md`: measured redundant-mask mechanism and verified prologue-only candidate.
- `../callback-allocation-pool-v1/REPORT.md`: 40 typed-count and assignment-flow probes across v1/v2.
- `../callback-allocation-selector-v1/REPORT.md`: selector relationships and further backend-driven probes in v2.
- `../callback-allocation-selector-v3/` through `v6/`: persistent-register repair, semantic replay, and subsequent stage-derived probes.
- `../callback-allocation-integrated/createCallbackTaskPreservingArgs.search.json`: fresh search and semantic receipt.
- `../callback-inverse-final/createCallbackTaskPreservingArgs.c`: retained 98.511 candidate.

The investigation demonstrates why retaining structurally different, lower-scoring candidates can help: the 98.511 global allocation residual became a 98.156 temporary-cycle residual, then a verified 97.979 opening-block-only residual. The next question is how the source can preserve the desired narrowing operation and parameter-assignment schedule through final assembly. Current experiments do not establish that exact source reconstruction is impossible.

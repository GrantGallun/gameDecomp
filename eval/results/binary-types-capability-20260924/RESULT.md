# Result: 277 new byte-exact functions from binary-derived types, with no reference and no model

Protocol: `PROTOCOL.md` (written before the run). Frozen method: `../context-ablation-20260924/freeze.json`.

## Harness run (`capability.py` -> `summary.json`)
All 1,732 workspace functions with no exact attempt in the KB. Their reference source was never read. Outcomes: exact
326, compiled 606, not compiled 749, no definition 45, m2c failed 6. Exact by size: small 311, medium 14, large 1.
322 of the 326 were exact without relocation masking.

## Confirmation and recording (`record.py` -> `inventory-receipts.json`)
Contamination screen: all 326 passed (no `#include "game/`, no type name found only in the reference's `src/` or
`include/game/**`). Official recompile in isolated repos through the frozen `compile_logged`, frontend gate included:
- **277 confirmed** (264 as drafted, 13 after the existing `frontend_type_repair` added prototypes).
- 49 not confirmed (the gate refused C that the repair could not fix; mostly draw functions).
Strategy `binary-types-20260924:source-independent`; ratchet check passed.

Of the 277: 266 had never been attempted before and 11 had; 17 are in sealed evaluation sets; 2 are in the restart
round-3 stuck population (`getRaceCourseNextSurface`, `packFixedTransformMatrix`; `osSpTaskStartGo` was already
counted exact there). Median 17 instructions, maximum 104, 54 longer than 30 instructions.

## eval.status after
705 byte-exact of 1,387 attempted; SOLVED 590 (before this run: 393 exact in the KB; the morning's printed status
was 381 / 275). **Read with two caveats:**
- eval.status counts every object-exact attempt, including ones whose C the frontend gate refused. About 35 of the 49
  unconfirmed functions are in that state. By the gate-passing standard this run adds 277, which is roughly 670
  byte-exact overall.
- The type-flywheel audit (same day) found about 172 of the previous 278 SOLVED depend on the reference team's
  headers. These 277 are clean by construction: binary facts, placeholder names, the public SDK prelude.

## What made it work (each measured, dated in the protocols)
1. Types, not compiler quirks, are the lever. With the reference's context m2c is exact on 34% of mining functions;
   without it 3.7%. Layouts and prototypes are both required (`../context-ablation-20260924/`).
2. Struct identity from binary dataflow with union-find (`../struct-identity-20260924/`): only dereferenced
   parameters carry type evidence; callback co-passing; binary arity; CFG dataflow. CHECK precision 0.979, recall
   0.547, layout agreement 0.989 against the reference headers, used only as the answer key.
3. A context generator from those facts (`../context-ablation-20260924/binary_context.py`): 25.8% exact on 450
   held-out mining functions, 69% of the gap between no context and the reference's.

## Limits
- Mostly small functions: the medium/large residual is the structural wall (branch shape, allocation), not types.
- One threshold (D32) was chosen with reference labels on the FIT half. That is disclosed, and no reference information
  reaches any function's context.
- The generated C uses placeholder names (`T12`, `unk24`). It is matching C, not readable C; naming is separate work.

## Round 2 (same day, after "go next"): generator v2/v3, gate repair, search
Each step has its own protocol file here, written before it ran. Confirmed = official recompile, frontend gate passed,
contamination screen passed, recorded.
| step | what changed | new confirmed |
|---|---|---|
| run 1 (above) | frozen generator | 277 |
| gate repair 1 (`fix_gate.py`) | existing `solver.frontend_fixits` casts at the gate's own diagnosed call arguments, on object-exact sources the gate refused; objects stay exact | 38 |
| generator v2 (`PROTOCOL-v2.md`) | identity v4 table elements, stride forms, no own prototype in the compile header | 14 |
| generator v3 (`PROTOCOL-v3.md`) | m2c `--valid-syntax` + existing `m2c_byte_view.lower`, on v2's not-compiled drafts: 236 more compile | 5 |
| search (`PROTOCOL-search.md`) | existing `regalloc_mutations.variants`, greedy, 48 compiles, on 670 compiled drafts: 106 exact (16%) | 99 |
| gate repair 2 | the same repair on newer refusals | 7 |
| search 2 | the 236 drafts v3 made compilable (mostly medium/large) | 1 |
| **total** | | **441** |
Families on the search's exact paths: field_local 80, owner:drop_mask 38, residual_evidence 21, stmt_move 14,
o1_register_saved 9 (the family built this morning), commutative 6, select_else 2. The pipeline's repair search was
effective once the drafts had binary-derived types.

`eval.status` after: **834 byte-exact of 1,510 attempted, SOLVED 719** (the morning's figures were 381 / 275). The
same caveat applies: eval.status also counts object-exact attempts whose C the gate refused, and later recorder runs
re-logged earlier confirmations as duplicate attempts (the counts are per function, so this does not inflate them).
Still open: 421 not-compiled, 387 improved-not-exact, 412 flat. The medium/large residual is the structural wall.

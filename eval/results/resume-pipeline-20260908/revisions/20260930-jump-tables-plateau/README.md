# Amendment: jump-table certificate + plateau-search profile

Status: **PREPARED, not applied.** Owner, 2026-09-30: "please do", in reply to the proposal to deploy the jump-table
certificate and the plateau search. Applying changes the live campaign and needs the owner's go.

Context: the mined lane (Engines A/B, the shape generators, budget 72) is already live. It was installed by
`../20260930-mined-lane/` at checkpoint 36058, not by this package.

## Change

| file | frozen → installed |
|---|---|
| `solver/function_boundary.py` | = main: schema 3 admits jump tables |
| `solver/repair_queue.py` | frozen + `plateau_digest`, `plateau_profile`, scheduling after the site-edit profile, band -1 |
| `eval/completion_campaign.py` | frozen + the `plateau` dispatch branch (stale-revision check) |
| `solver/plateau_search.py` | new |
| `eval/plateau_repair.py` | new: the site-edit route's runner and promotion rule around `plateau_search.search`, budget 240 |
| `solver/nearmiss_llm.py` | new, inert: only reached when `llm_repo` is passed, and the campaign never passes it |

**Jump tables.** A rodata relocation is admitted only if it meets every condition:
- it is an R_MIPS_32 against the object's own `.text`, read straight from the ELF, so a non-ALLOC `.late_rodata` is
  checked too;
- every entry is word-aligned inside the certified function;
- the candidate reads the table with `lw` at a site whose target counterpart reads the target's table;
- the linked table words equal the ROM's.

Pointer tables to other functions stay refused. Negative controls in `tests/test_function_boundary_jump_tables.py`:
an entry moved one instruction, and an entry outside the function, are both refused.

**Effects at install.**
- The certificate digest changes, so the existing census `recertify@` profile re-scores score-100 and
  relocation-only pending nodes once.
- `plateau@` visits pending nodes on the site-edit gate (at most 12 non-layout faults, compiled, frontend-valid),
  once per source, only after that node's `site_edits@` visit.

## Evidence (`eval/results/loop-shape-20260930/RESULTS.md`)

- Re-scoring all 832 unsolved best candidates under the new certificate: 36 newly function-exact (34 with a jump
  table). Each integrated alone with the campaign's own `prepare_integration` + `integration_gate`: **28 rom_exact
  (whole ROM verified)**, 7 blocked by shared-declaration preparation, 1 build_failed.
- Plateau search on the 219 near misses the site-edit search left: **5 exact** in 47,345 compiles.

## What this install does NOT do (owner decision)

**Provenance:** 31 of the 36 jump-table candidates start from `authorized-target-history-recovery` or
`historical-provenance-exact-source`. That is the recovered tier: reference-derived, not capability.

And 32 of the 36 best candidates live in the `kb` ledger, not the campaign's. The campaign nodes retain other, worse
sources, so the recertify pass will not reach them. Landing those needs a separate import step, one that brings kb
candidates into the campaign ledger with their original strategy and tier kept. That is a node-and-ledger change, so it
is not in this machinery-only amendment.

## Apply order

1. Set both pause markers and let in-flight work drain. The campaign service is running now.
2. `stage.py`, then `verify_stage.py`, then `apply_amendment.py --apply`.
3. Remove the pause markers and let the service continue.

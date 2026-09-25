# Protocol: mining the allocation inverse (which C edit fixes which register mismatch) from the campaign's own edges

Written 2026-09-24 before `mine_alloc.py` existed. Motivation: `frontier-20260924` — allocation is about half of every
residual; 130 campaign functions have zero structural differences and only register/operand ones. The forward model
exists (uopt trace, 99.9% of decisions); the inverse (edit -> allocation effect) does not. The campaign ledger holds
155,115 parent->child edges with both sources and both diffs. These are our own compiler observations, not reference
data.

## Edges used
`attempt_edges` in `runs/resume-pipeline-20260908/campaign.sqlite`: parent and child compiled, same function, parent
residual has >= 1 register step and <= 2 structural steps (the allocator regime; steps from
`eval.mechanism_roadmap.classes` on the recorded diff: `field:register` = register step; extra/missing/opcode =
structural). Deduplicated by (function, parent source sha, child source sha).

## Parent mismatch type (from the parent diff only)
For every register step, the (target register, candidate register) pairs at differing operand positions. Register
classes: v (v0-v1), a (a0-a3), t (t0-t9, at), s (s0-s8, fp), other. Pair kinds: `same-class` (t<->t, s<->s, ...),
`t<->s` (a value on the wrong side of the caller/callee-saved split), `a<->t/s`, `v<->*`. The parent's type is its most
frequent pair kind (ties: `mixed`).

## Edit type (from the SOURCE diff of the function body, whatever mechanism produced it)
Lines normalized (whitespace). `decl_order`: same multiset of lines, only declaration lines moved. `stmt_order`: same
multiset, statement lines moved. `operand_swap`: exactly one line changed and its token multiset is unchanged.
`local_type`: exactly one declaration changed only in its type. `temp_intro` / `temp_remove`: one more / one fewer local
declaration. `cast`: only casts added or removed. `register_kw`: only `register` added or removed. `multi`: several of
these; `other`: none.

## Outcome
`fix`: child register steps < parent register steps AND child structural steps <= parent's. `exact`: child exact.

## Controls (instrument checks)
- Negative: edges whose child diff equals the parent diff must have fix = 0 (else the step accounting is broken).
- Positive (fires): every edge whose child is exact from a parent with register steps must count as a fix.
If either fails, the instrument is fixed before any association is read.

## Rule candidates
(mismatch type, edit type) cells with >= 30 deduplicated edges from >= 10 functions, fix rate >= 20% and >= 2x the
edit type's fix rate over all mismatch types. Reported with the exact-child count. Reading: rule candidates are
hypotheses; each needs a paired synthetic test, then a mechanism fired on the 130 zero-structural functions. If no
cell qualifies, the edges do not carry a mismatch-conditioned inverse at this resolution: report the per-edit fix rates
and stop.

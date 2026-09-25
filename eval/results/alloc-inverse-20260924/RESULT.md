# Result: the allocation inverse is not in the edges at register-class resolution; temp removal is spent

`mine_alloc.py` -> `analysis.json`; `temps_left.py`. Protocol: `PROTOCOL.md`.

## Registered outcome: null (no rule candidates)
14,898 deduplicated edges in the allocator regime (parent: >= 1 register step, <= 2 structural). Controls: positive
passes (all 210 exact children count as fixes); negative is untestable (source deduplication left no edge with an
unchanged diff). Mismatch kinds: **same-class 11,960 (80%)**, t<->v 862, a<->v 817, mixed 694, a<->t 490, a<->s 74,
s<->t 1. No (mismatch, edit) cell reached the bar, so conditioning on register class adds nothing: the mismatches are
the right kind of register with the wrong index, which is allocation ORDER, not class.

## Per-edit fix rates (unconditioned) and exact children
| edit (from the source diff) | edges | fix | exact children |
|---|---|---|---|
| temp_remove (inline a local) | 186 | **68%** | **99** (90 in same-class) |
| local_type | 196 | 17% | 2 |
| decl_order | 309 | 16% | 0 |
| stmt_order | 3,075 | 8% | 13 |
| temp_intro | 369 | 8% | |
| operand_swap | 5,127 | 1.6% | |
| cast / register_kw | 187 | ~0% | |

## Reading
1. Historically, allocation-only residuals close by REMOVING m2c's temporaries: 90 exact children from 146
   same-class temp_remove edges over 120 functions. The campaign's tools already do this (`field_local` and kin).
2. **The lever is spent on the current pool**: of the 130 zero-structural functions, 108 have no single-use local left
   (the rest are mostly stack slots such as `sp20`, which cannot be inlined), and the campaign tried temp removal on
   40.
3. decl_order and local_type move registers (16-17% fix) but never closed a function alone: they permute allocation
   order without explaining it.
4. What is lacking, precisely: for real variables (not m2c temporaries), the cause of allocation order: which live
   range claimed a colour first and why (uopt priority: references, loop depth, span). The forward trace model knows
   this per decision; nothing yet maps it to a source edit. Next measurement: the existing allocation census
   (`alloc-inverter-20260923`: blocked / selection / split / ugen_temp) run on exactly these 130, to see whether their
   first wrong ranges are edit-reachable causes (blocked, ugen_temp) or the unexplained `selection` kind.

# Mechanism potential, in information terms: the diff often states the fix; the mechanisms cannot place it

No compiles. Inputs: 25,334 recorded search nodes (four population arms) and the machinery card.

## Framing

The compiler's diff is a message about what is wrong. For some residual classes it states the correct value
outright (`-lw v0,0x280(a0) / +lw v0,0(a0)`: the field belongs at 0x280), so a mechanism that used all of the
evidence would need one candidate. For others (register allocation) the diff says *that* something differs but
not which source edit causes it, and search is unavoidable. A mechanism's **potential** is the information its
target residual carries about the fix; its **ability** is how much of that it converts.

## Coverage of the residual each mechanism exists for (`demand.py`)

| Residual | Functions with it | Owner fired | Coverage |
|---|---:|---:|---:|
| offset (value stated by the diff) | 132 | 33 | 25% |
| stack spill | 104 | 31 | 30% |
| immediate (value stated) | 128 | 53 | 41% |
| mask (target has none) | 101 | 42 | 42% |
| reloc (symbol stated) | 117 | 50 | 43% |
| width | 107 | 84 | 78% |
| ordering (search-like) | 115 | 114 | 99% |

## Information use per candidate (`information.py`)

Evidence readers (`global_load_signedness`, `frame_padding`, `residual_evidence`, `layout`, `per_object_layout`,
`drop_mask`) offer 1-2 candidates and succeed 8-19x the base rate (+2.8 to +4.2 bits over blind mutation).
Blind searchers (`local_type`, `commutative`, `decl_order`, `stmt_move`, `argswap`) offer 9-18 and do worse than
the average candidate. `owner:immediate` is the extreme gap: its residual states the constant, and it improved
0 of 24.

So the potential is concentrated where the evidence is strongest, and the loss there is reach, not efficiency.

## Why the offset owners are silent (`offset_gap.py`)

Loose pairing overstates the evidence: with `diffrepair.aligned_pairs` (ambiguity-aware) the untapped offset
residual falls from 99 functions / 977 pairs to **69 functions / 369 pairs**. Of those pairs the candidate's
offset is spelled in the source as a declared struct field for 98, a literal for 22, and **not at all for 249**.
A fix needs the value (the diff has it) and the place (which C token produced that access). The place is the
missing information, and the pipeline already records it: `source_attribution` maps each compiled
instruction to its source line on every attempt, and no mechanism reads it.

## Next

Connect `source_attribution` to the evidence-reading owners (offset, immediate, reloc, mask): locate the source
site of each stated fault, then apply the stated value there. Measure with `eval.machinery_card` and the demand
table: coverage of evidence-determined residuals should rise toward the 78-99% of the search-like classes
without the per-candidate success rate falling.

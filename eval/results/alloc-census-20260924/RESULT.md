# Result: of the 130 "structurally correct" campaign functions, the allocator proper owns about half

`census.py` -> `census.json`. Protocol: `PROTOCOL.md`. 108 diagnosed (13 declined, 9 not traceable).

## First wrong live range (colouring order)
| class | functions | register-only | residual |
|---|---|---|---|
| **none** (every coloured range agrees with the target) | **50** | 10 | operands: shape 44, offset 35, immediate 32, symbol 13, multi 11 steps |
| selection (the target colour was free; the rule chose another) | 25 | 18 | registers |
| split (range structure differs) | 21 | 12 | registers |
| ugen_temp (a compiler temporary that should not exist) | 11 | 8 | registers |
| blocked | 1 | | |
Registered verdict: **mixed** (blocked + ugen_temp 11%; selection 23%).

## Reading
1. The biggest group is not an allocation problem. In 50 functions uopt's colouring already agrees with the
   target; 40 of them carry OPERAND faults, and the classifier marks 80 of their 136 operand steps "stated" (the diff
   gives a source-expressible value, symbol or width). Their register differences follow from those operands. This is
   the deterministic diff-driven repair front (evidence_site, diffrepair, global/field owners), plus addressing form:
   `field:shape + field:symbol` (10 functions) is `%lo(sym+off)` against base-plus-offset, a global declared as a
   struct against pointer arithmetic, which binary-derived declarations can express.
2. ugen_temp (11) has generator families named in `uopt_diagnosis.CLASS_FAMILIES`; the campaign's search has tried
   them without closing these.
3. selection (25) and split (21) are genuine allocator questions with no mapped edit: the selection/priority rule and
   live-range splitting (IDO gaps #3, not started). That is compiler research, not mechanism building.
4. Combined with `alloc-inverse-20260924`: temporary removal (the historical allocator closer) is spent here; what
   remains is research-grade (46 functions) or operand repair (40 functions).

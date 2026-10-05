# IDO 7.1 allocator rules tested on IDO 5.3: two confirmed, four refuted or corrected, three new

Protocol fixed first (`PROTOCOL.md`). Evidence is 5.3 only: uopt's own trace from the gated compiler over all
115 uopt-compiled SBK1 TUs (1,976 procedures, 11,809 ranges, 11,310 decisions; `results.json`), plus 30
paired compiles of synthetic `syn_f` functions (`interventions.json`). 7.1 claims from akratch's
instrumented-uopt allocator model.

| 7.1 claim | IDO 5.3 | Verdict |
|---|---|---|
| Webs coloured in descending priority | constrained ranges: 98.9% of 8,640 pairs | **holds for constrained** |
| Ties by first source appearance | 99.6% of 3,471 ties in live-range (first-store) order | **confirmed** |
| ...for every web | unconstrained: 66% by priority, **100%** of 875 by live-range order | refuted for unconstrained |
| Lowest free register | 89.5%; this project's 5.3 preference model 99.9% | refuted (preferences matter) |
| Conflict iff a shared live block | P(conflict given shared) 0.80, P(none given disjoint) 0.94 | refuted |
| priority = 10·refs / units(refs + blocks) | 12.6% (shuffled 11.9%) | refuted |
| `if (!x);` = one more reference | +1 save, same as a read, 3 of 3 bases | **confirmed** (weight differs) |
| Bare `x;` discarded by the front end | object and priority unchanged 3 of 3; trace text differs | object claim holds; pre-registered trace criterion failed |

New 5.3 facts:
- **priority = integer save / units(block span)**, with 7.1's step function on the block count alone:
  11,808 of 11,809 ranges (shuffled 69.7%). Post-hoc, read off the refuted form's misses.
- **Save is +1 per read and +0 per write** (3 of 3 bases each), and can go non-positive: a local crossing two
  calls had adjsave 0 and stayed in memory, so 5.3 subtracts a cost the 7.1 description omits.
- **`if (!x);` is a zero-code priority knob**: in both leaf bases it changed x's priority and not one
  instruction.

Entered in `patterns/catalog.py` as `uopt53-*` with `confirmed_on`. Nothing here changes solver behaviour yet.

## Two bugs found by running it
- The first intervention run "confirmed" H5a by comparing two missing measurements (`None == None`): the
  bases never gave x a register. Verdicts now say `untestable` when either side is unmeasured.
- `solver.family_gates` classified every function as libultra: all 193 SBK1 compiles define
  `COMPILING_LIBULTRA`, game code included. The recipe feature now reads `C_OPT` (-O1 libultra, where uopt
  does not run; -O2 game). None of the three installed gates used that feature.

## Open
Loop weighting (the loop split the test range), the cost term's form, and the 20% of shared-block pairs that
do not conflict. The next use is a register mechanism that reads the trace: which neighbour holds x's target
colour, and whether one more read (`if (!x);`) or an earlier first store flips it.

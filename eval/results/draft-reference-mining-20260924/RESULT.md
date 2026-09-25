# Result: reference corrections of m2c drafts, mined against the unsolved population

`mine.py` -> `analysis.json`. Protocol: `PROTOCOL.md` (written before any association was computed).

## Pre-registered verdict: mixed
| | all mining | TU-excluded |
|---|---|---|
| U (unsolved population with a residual) | 201 | 201 |
| covered-any | 196 (97.5%) | 197 (98.0%) |
| covered-all | 1 (0.5%) | 4 (2.0%) |

Positive controls: C1 fires (`S:count` -> `if-` lift 18.5, `else+` lift 16.3: the select rule of 2026-09-24,
rediscovered with no hint). C2 is **untestable**: only fewer than 10 mining drafts have a loop-shape residual.

## Data
1,558 mining rows. 1,400 usable (reference byte-exact modulo relocation names; 1,237 raw-exact). 422 drafts do not
compile, 3 have no draft. **471 raw m2c drafts are already exact** once given the reference's context. 504 pairs.

| size | usable | draft compiles | draft already exact | pairs |
|---|---|---|---|---|
| small (<50) | 793 | 707 | 437 (55%) | 270 |
| medium (<150) | 393 | 229 | 33 | 196 |
| large | 214 | 39 | 1 | 38 |

## Reading
1. **covered-any is inflated by generic style differences.** Most enriched families are weak (lift 2-4) and describe
   m2c versus human style, not a fix: fewer casts (`cast-`), subscripts instead of pointer arithmetic
   (`subscript+`), fewer temporaries (`stmt-`, `decl-`). Under a lift >= 5 floor, covered-any is 122 / 201.
2. **covered-all near 0 is partly a protocol flaw.** `S:same-skeleton` marks the ABSENCE of a structural fault
   and can never have a fix, yet it counted as a feature (140 functions). Post hoc, without it: covered-all
   54 / 201 at lift >= 2, 0 at lift >= 5. Exploratory, not a verdict.
3. **`R:field:offset` (73 functions) is invisible by construction.** The drafts were given the reference types,
   which removes offset faults from mining. That is the type axis, already owned by diffrepair.
4. **Specific, high-lift associations: mechanism candidates, NOT confirmed rules.**
   - switch: `extra:sltiu`, `extra:beqz`, `missing:slt`, `opcode:lh/lhu`, `opcode:li/sw` -> `case+`/`break+`
     (lift 49-111) together with `goto-`/`label-`. m2c renders a switch as a goto/if chain; the reference writes
     `switch`. This is the jump-table/switch wall CLAUDE.md names as the next action.
   - select: `S:count`, `missing:b` -> `else+`/`if-` (lift 11-18). Already mechanised in `solver/branch_shape.py`.
   - goto structuring: `extra/missing:sll`, `opcode:andi/move`, `opcode:andi/li` -> `goto-`/`label-` (lift 8-12).
   - `register` keyword: `opcode:move/sw`, `opcode:addiu/lw`, `opcode:nop/sw` -> `register+` (lift 13-32).
   - loop form: `missing:sra` -> `for+`/`do-` (lift 12).
5. **Selection bias.** Only 39 large drafts compile. The associations describe small and medium functions.

## Side finding
Given correct context, m2c alone is byte-exact on 55% of small mining functions (437 / 793). For small functions,
the gap between a draft and a match is mostly declarations and types, not C shape. This is in tension with the
2026-09 A/B, where reference types given to the LLM made it worse. The two are consistent if types help
deterministic drafting but hurt free generation. That is untested.

## Next (each needs its own protocol)
- A switch mechanism from the goto/if-chain draft, fired on population functions carrying the switch residuals.
- Fire the `register` association on population functions with `opcode:move/sw` / `addiu/lw`.
- Measure how many population functions carry a residual that only a specific association (lift >= 5) covers.

## Correction (2026-09-24, after RESULT.md): clone families, and the switch association is withdrawn
`dedup.py` -> `dedup.json`. One pair per clone key (target length, draft shape, reference shape): 504 -> 437 pairs,
specific associations (lift >= 5) 67 -> 27. **Every switch association disappears.** Its support was nine
near-identical `updateCharacterSelectCoursePreviewPanel{1..9}` pairs. In those pairs, m2c already writes a switch,
but it drops an empty `case 0: break;` and wraps the switch in its own jump-table range guard
(`if (var_at_2 != 0) switch ...`). That is a real idiom, and it reaches one population best node that contains a
switch (`osPiRawStartDma`). The population's "switch signature" features (`missing:slt`, `opcode:li/sw`, ...) are
generic opcodes with other causes. Item 4's switch line and the "next" switch mechanism are withdrawn.

Survivors, deduplicated: `register+` for `opcode:move/sw` (lift 33, 10/18) and `opcode:addiu/lw` (12.6); the
select rule (`S:count`/`missing:b` -> `else+`/`if-`, 13-15, already in branch_shape); goto structuring (`goto-`/
`label-` for `missing:slt`, `S:count`, `opcode:andi/move`, `missing:sll`, 7-11). Population covered-any under
specific, deduplicated associations: 112 / 201.
Population reach (best nodes, `switch-shape-20260924/show.py`): 26 functions carry a register signature (6 already
use `register`); 26 still contain gotos (40 in all).
Lesson: count associations by clone family, not by function; this game has many templated functions.

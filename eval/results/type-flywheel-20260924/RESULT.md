# Result: the type flywheel's harvest is mostly the reference team's types, and so is most of SOLVED

Question: can declarations harvested from the pipeline's own SOLVED matches (no reference source) supply the type
layer that makes m2c drafts compile and match? Stopped before the redraft arms, because the harvest audit found
that most SOLVED sources depend on the reference team's reconstructed `include/game/**` headers.

## Harvest (`harvest.py`, `coverage.py`, `informative.py`)
147 SOLVED sources passed the harvest filter (not recovered, header-assisted or reference-type-assisted, no
`#include "game/`, frontend gate not failed). They yield 147 verified signatures, 64 global externs, 50
prototypes and 20 struct definitions. Coverage of the 206 unsolved population functions' own references: globals
107/651 (16%), calls 122/392 (31%); 35 functions at least half covered. Of the harvested aggregates, at least 12 of
20 carry type names defined in the reference's `include/game/**` (`RacePlayer`, `GameTask`, `CallbackTask`,
`RaceCamera`, ...).

## SOLVED audit (`game_type_leak.py audit` -> `solved_audit.json`)
Of the 278 SOLVED functions with a logged exact source, **189** have EVERY exact source either `#include`-ing a
`game/` header (128) or using a type name defined only in `include/game/**` headers (153). 89 are clean by this
test. eval.status's header-assisted tier keys on the strategy string only; its comment records the `#include` gap
as known and deliberately unmeasured.

## Measurement (`strip_ablation.py` -> `strip_ablation.json`; control `strip_control.json`)
The latest exact source of each flagged function is recompiled in a copied workspace with every `#include "game/..."`
line removed. Control: all 189 unmodified sources are exact in the same harness.
| outcome | functions |
|---|---|
| fails to compile without the game headers | **124** |
| still exact, but defines a game-only type locally | 60 (`local_layouts.py`: **48** with named members, such as `pitchBendDepth` or `env_trigger_off`; 12 with only offset-derived members) |
| still exact and no game-only type (the includes were vestigial) | 5 |

## Reading
- Of the 278 SOLVED, about **94 are clean** (89 plus the 5 vestigial) and 12 more borrow only a type name over an
  offset-derived layout. **172 depend on the reference team's headers**: 124 need them to compile, 48 copy
  named-member layouts. SOLVED as printed overstates binary-reachable capability by about 2.6x.
- Caveat: `include/game/**` also holds libmus/audio library declarations, a public library rather than game-specific
  vocabulary. Some of the 48 (`F*` libmus handlers) are in that category. Not separated here.
- This moves no match and changes no tier; eval.status says reclassifying is the operator's call. It is also the
  strongest evidence yet for the question that started this: **the type/declaration layer is what turns drafts into
  matches**. Most of the pipeline's successes had it, from the reference team's headers.
- The flywheel as designed (harvest from SOLVED) would recirculate that assistance. A clean flywheel has to harvest
  only from the ~94 clean functions, or build types from binary evidence. Its current clean coverage is too small to
  measure a redraft effect.

# Iterations 6–9: the whitelist, the pointer wall, and two of my own rules measured wrong

| receipt | IDO | IDO+frontend | byte-exact | change |
|---|---:|---:|---:|---|
| `revert` (end of LOOP-4) | 48 | 29 | 3 | — |
| `whitelist` | **53** | 30 | 3 | clang's names reach `undeclared_identifiers` through a positive whitelist |
| `widen` | 53 | **31** | 3 | `widen_pointer_declarations` added |
| `widen2` | 53 | 31 | 3 | the pass's column and wording defects fixed |
| `widen3` | 53 | **34** | 3 | struct pointees adopted where the candidate is already header-assisted |

**No state was lost in any of the four.** Session total: IDO 32 → 53, IDO+frontend 16 → 34,
byte-exact 2 → 3.

## 6 — the whitelist (+5 IDO, 0 lost)

LOOP-4 merged clang's names into `undeclared_identifiers` raw and lost 7 states, because clang names
type names, m2c temporaries and `bitwise` as readily as data. The fix is a POSITIVE gate reusing
`eval/name_triage`'s own patterns: `gFoo`/`sFoo` (m2c's naming for a translated global) and
`D_<8 hex>` (the name is the address, and the relocation carries it). Everything else is counted in
`frontend_names` and not declared.

It beat the raw merge in both directions — 5 gained instead of 4, 0 lost instead of 7 — because it also
admitted `D_801121E0`, which the raw version had buried among the junk. Seven tests pin each name that
broke the ratchet, by name.

## 7 — the wall the declaration passes built (+1)

`incompatible-int-pointer` became the top sole blocker (65 states, 8 sole) *because* iteration 6
worked: every converted state hit it next. The cause is conceptual — the declaration passes type a
datum from access WIDTH, and a width is not a type, so `extern s32 X;` is asserted where the use needs
`u16 *`. `solver/pointer_decl_widen.py` replaces that extern with the type **clang states the use
requires**, and only ever edits an extern this route itself emitted.

`bare p = X;` → `extern T X[];`; `indexed p = X[i];` → `extern T *X[];`.

A note on reading receipts: `per_action` for this pass reads `fired: 0`, and that is correct. It is
the ISOLATED arm, where each action runs on the raw draft; this pass edits what earlier passes emit, so
alone it has nothing to act on. In-sequence it changed 5 candidates.

## 8 — two defects in that pass, both assumed rather than measured (0)

1. **The column.** I wrote that clang's column "differs per wording" and fell back to "the one declared
   name on the line", which abstained on `drawMainMenuModeDescriptionPanel` where the array and its
   index are both declared. Measured against clang 20.1.2, the column points at the START OF THE
   CONVERTED EXPRESSION in all four wordings.
2. **The regex understood one wording of four.** `assigning to`, `initializing`, `passing` and
   `returning` put the pointee in different places, and `[^']*` cannot cross a quote. The pointee is
   now the first quoted type ending in `*`.

Zero movement, because the states these unlocked were still gated by the abstention below.

## 9 — the boundary was protecting purity that had already been spent (+3, 0 lost)

The pass refused every struct pointee on the grounds that adopting `MenuGlyphScript *` would make a
repair header-assisted. Measured on the states it refused:

| state | `game/**` includes |
|---|---:|
| `drawRaceSetupPlayerCountPrompt` | 5 of 6 |
| `waitCourseSelectRecordsClose` | 6 of 7 |
| `acquireSoundEffectHandleNode` | **0 of 1** |

For two of three, `header_variant` had already taken the assistance, and clang could only name the
type because it was in scope. **The tier is a property of the candidate, not of the pass.** Struct
pointees are now adopted only where a `game/**` header is already included; `acquireSoundEffectHandleNode`
still abstains, and it is the one case the boundary genuinely protects.

**All three gains are HEADER-ASSISTED** and must not be quoted as SOLVED capability. Each plan records
`pointee_is_primitive` and `game_headers`, and the receipt's `authority` reads
`HEADER-ASSISTED: struct pointee from <header>`. That also closes, for this route, the gap
`eval/status.py` documents about itself: it detects header assistance only by strategy string and is
blind to the `#include` route.

## The pattern across all four

Three of these four iterations corrected a rule I had written from reasoning instead of measurement —
the column, the wording, the boundary. CLAUDE.md says every analysis bug so far had that shape: *a
hypothesis about compiler behaviour encoded as a rule, without testing it.* Each fix was one clang
invocation or one grep once I stopped assuming. The rule I should have followed: **before a pass
abstains on a class, measure the members of the class it is abstaining on.**

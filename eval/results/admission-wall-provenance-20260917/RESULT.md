# The admission wall: where the named member actually comes from

**Date:** 2026-09-17 · **Model calls:** 0 · **Ratchet:** 303 byte-exact / 237 SOLVED (unchanged this round)

The objective asked one question that is answerable from the repository rather than by reasoning:
`typedecl.plan` declines on `PlayerCommandState *var_s0;` because one member name cannot be placed among
60 pooled offsets, and the refusal is correct. So where does the name come from? If a project header
declares the type, the header route is the honest fix. If nothing does, the name is unattributable from
binary evidence and the only honest route is an `unk`-only declaration plus a rewrite of the unresolved
uses. **The answer is: both, and which one applies is mechanically decidable per name.**

## 1. The names come from `ctx.c`, which `tools/claude` builds from the project's own source

`external/snowboardkids-decomp/tools/claude`:

```
252:        C_FILE="src/${RELATIVE_DIR}.c"
258:            if python3 tools/m2ctx.py "$C_FILE" 2>/dev/null; then
265:        M2C_OUTPUT=$(m2c --target mips-ido-c $CONTEXT_ARG "$ASM_PATH" 2>&1)
```

`C_FILE` is the function's own file under `src/` — for SBK1, the **reference decompilation** — and
`m2ctx.py` turns it (headers plus that file's own declarations) into `ctx.c`, which is handed to m2c as
`--context`. That is why m2c emits type names it could not have derived from assembly: it was given
them. Bootstrapping leaves `ctx.c` (365 KB) and `ctx.c.m2c` (1.4 MB) in the repo root, and 0 of the
workspaces keep a copy, which is exactly why this was invisible until the names were traced.

Evidence that the declaring file is the *same* file as the function's source:

| name | declared in | drafted functions |
|---|---|---|
| `CourseGridEntry` | `src/race/flow/race_flow.c:54` | `clearRaceReplayCourseGrid`, `saveRaceReplayCourseGridEntry`, `loadNextRaceReplayCourseGridEntry` |
| `RaceCourseSurface` | `src/race/motion/race_motion.c:31` | the five `*RaceCourseSurface*` drafts |
| `PositionalSoundRequest`, `SoundQueueEntry`, `SoundRomRange` | `src/audio/sound_manager.c` | the sound-manager drafts |
| `RaceItemDrawNode`, `RaceItemEffectPayload` | `src/race/items/race_item_effects.c` | the item-effect drafts |

## 2. The mechanical routing decision, over every never-attempted draft

`eval/type_name_provenance.py`, over the never-attempted drafts with statements:

| | n |
|---|---|
| never-attempted drafts with statements | **1,197** |
| never-attempted with no draft at all | 14 |
| distinct non-primitive type names in those drafts | **75** |
| — declared by a project **header** | **53** → header route |
| — declared only by a project **`.c`** | **21** → `unk`-only route |
| — declared nowhere at all (`Mtx_t`, almost certainly a draft typo for `Mtx`) | 1 → `unk`-only route |
| functions whose missing types are ALL header-resolvable | **174** |
| functions carrying at least one header-less name | **31** |

`eval/match_claim_audit.py --types ...` produced the middle rows name by name. Nothing here is
inferred from naming convention: each name is traced to a file and a line.

## 3. A detector bug that was misclassifying names as evidence-free

`declaring_header` recognised `typedef struct Name {` and `} Name;` but **not a plain
`typedef <base> Name[...];`**. `Mat3x3` is declared at `include/game/math/geometry.h:23` as
`typedef s16 Mat3x3[9];` and the function returned `None` for it.

This mattered more than a missed fix. A `None` from this function is not a neutral answer — it is what
routes a name to the `unk`-only path as *unattributable from evidence*. The miss was a
misclassification. Fixed, with `tests/test_declaring_header.py` asserting all four spellings fire and
that two neighbouring shapes do **not** (`typedef s32 (*Fn)(RaceCourseSurface *);` uses the name as a
parameter; `initRaceCourseSurfaceData` contains it as a substring).

Measured effect: **53 names resolved, up from 50** (`Mat3x3`, `Gsettilesize`, `Gloadtlut` moved), 22
header-less, down from 25.

## 4. What this means for the tier accounting — the part that matters

The 21 `.c`-only names were handed to m2c from the reference decompilation. Therefore:

> **A draft produced by `tools/claude` at bootstrap time may already contain reference struct layouts,
> and a byte-exact match built on one is not independent capability.**

`eval/match_claim_audit.py` is the check: it collects every non-primitive type name a source mentions,
asks where each is declared, and reports a tier ceiling.

| this round's matches | ceiling |
|---|---|
| `loadMainMenuSceneModelAnimationBank` | **unqualified** |
| `__osViGetCurrentContext` | **unqualified** |
| `alFxParam` | **unqualified** |
| `resetRenderScratchAllocator` | **unqualified** |
| `guMtxIdent` (near-miss) | **unqualified** |

All five rest on no project declaration at all, so the four SOLVED claims from the previous round
stand. That is not luck: the three redrafts came from m2c invoked **without** `--context`, and
`loadMainMenuSceneModelAnimationBank`'s draft happens to use no project type.

Two consequences to carry forward, both falsifiable:

- The **174 header-resolvable functions can be admitted, and their ceiling is `header-assisted`, not
  SOLVED.** They are worth admitting — a scored draft is triage material — but they must not be
  reported as capability.
- The **31 functions carrying a `.c`-only name** are the ones the `unk`-only route owns, and they are
  also the ones where a match would be most misleading. `eval/match_claim_audit.py --functions` is the
  gate: run it on any candidate whose draft predates this round.

## 5. Work item (3): compiled / exact / new terminal classes

Run over the header-resolvable population: `--only-resolvable` kept **312 of 1,557** never-compiling
drafts, budget 2,400 s, 312 rows processed.

| | n |
|---|---|
| **compiled (ADMISSION, not a match)** | **17** |
| **exact** | **2** — `loopMainMenuSceneModelAnimation`, `notifySchedulerClients`, both 100.000 |
| not-compiling | 295 |

An 5.4% admit rate here, against 22% for the unrestricted stratum — the filter removes the ~1,300 rows
that were never this route's business, and it keeps 312 that are. Receipts:
`eval/results/header-admission-resolvable-20260917/state.json`,
`eval/results/header-admission-resolvable.log`.

The first version of `--only-resolvable` kept **1,484 of 1,557**, because `all([])` is True and a draft
with *no* missing types is vacuously resolvable. That is the flag's entire content, and getting it
wrong reproduced exactly the waste the flag exists to prevent. Caught by reading the kept-count line
before the compiles landed.

**Both new matches are `header-assisted` by source audit, and both are counted as SOLVED.** See §6.

Residual classes, in the order they appear on a draft (from the previous round's 122-draft composition,
which is the calibration for this one):

1. m2c's `?` type placeholder — owned now by `solver/m2c_placeholders.py`
2. undeclared parameter/type names — owned by this route when a header declares them
3. m2c's `(bitwise T)` cast token — 6 drafts, 9 occurrences, measured small
4. `? sp30;` used later as `sp30.unk0`, so `s32` is the wrong answer for it — needs a variant, not a
   substitution; `m2c_placeholders.variants` bounds it and is not yet wired in
5. `ObjectBackendRequired` — 5 rows, a harness gap, not a draft defect

## 6. Round 4: the honest route for a contaminated draft is a re-draft, not a rewrite

The objective's item (2) says: if no header declares the type, the honest route is an `unk`-only
declaration plus a rewrite of the unresolved uses. **The evidence says that is the wrong instrument,
and the measurement is the reason.**

For the 21 `.c`-only names, the offsets and member names in the draft came out of `ctx.c`. Stripping
the type and writing byte arithmetic over the same offsets is the same knowledge laundered: the draft
still *knows* the layout, so an `unk`-only rewrite would buy the appearance of independence and none of
the substance. The instrument that actually removes the contamination is to ask m2c again **with no
`--context`** — which is what `eval/m2c_redraft.py` already does, and why its three earlier matches
audit `unqualified`.

Run: `eval/m2c_redraft.py --provenance ... --sidecar --admit` over all 31 affected drafts, writing
`base.contextfree.c` **beside** `base.c` rather than over it — the contaminated draft is the evidence of
the contamination and is not destroyed.

| | n |
|---|---|
| drafts carrying a header-less type | 31 |
| re-drafted context-free | **29** (2 refused by m2c) |
| **compile** | **0** |
| still not compiling | 28 |
| skipped — suffixed workspace (`drawRacePlayerModel-2`) | 1 |

Mechanism check, which is the part that must fire: no sidecar draft uses any of the project types as a
type. `clearRaceReplayCourseGrid` goes from `CourseGridEntry *var_v0;` / `var_v0->status` to
`s16 *var_v0;` / `*var_v0` with `var_v0 += 0x10` — the layout is gone, replaced by the arithmetic the
assembly actually shows.

Residual classes on the context-free drafts: `Syntax Error` 26, `Selector requires struct/union pointer
as left hand side` 2, suffixed-workspace 1.

**So the honest answer for these 31 is that there is no route from m2c alone.** Re-drafting removes the
reference layout and the draft stops compiling; keeping the layout compiles some of them but the
result cannot be called capability. What is left for them is a model draft (not available: no endpoint)
or the reference layout, which is `header-assisted` at best and must be reported as such.

One correction to my own check: a first grep said 5 sidecar drafts "still contained the project type".
All five were the function's own name matching as a substring — `findRaceCourseSurfaceFromHint` is not a
use of `RaceCourseSurface`. Re-run against the type used *as a type* (`Name *` or `Name;`), the count is
zero. The naive check would have reported the mechanism as broken.

## 7. A tier-count discrepancy, reported rather than patched

`eval/status.py` keys `header-assisted` on the winning attempt's strategy string:
`a.strategy like '%project-header%'`. The header route logs `strategy="header-admission:*"`, which does
not contain that substring — so its matches are classified **SOLVED** even though the route's whole
definition is "a reconstructed `include/game/**` header supplies the prototype and the layout".

`eval/match_claim_audit.py` on both new matches:

| function | types | audit verdict |
|---|---|---|
| `loopMainMenuSceneModelAnimation` | `MainMenuSceneModel` | header-backed → ceiling `header-assisted` |
| `notifySchedulerClients` | `SchedulerClient`, `SchedulerState` | header-backed → ceiling `header-assisted` |

So the honest numbers are **305 byte-exact, 239 SOLVED by the strategy-keyed rule, 237 SOLVED by the
source-based audit.** `eval/status.py` carries a comment on exactly this tension — it notes that an
include-based test would reclassify existing SOLVED matches and lower the count, and that whether that
is a correction or a regression is the operator's call, needing a check across the whole matched set
rather than a patch for one match. That is still true, and this round does not decide it:

- Relabelling these two attempts to include `project-header` would move 2 from SOLVED to
  header-assisted. The number would fall, which reads as a ratchet violation — but the ratchet protects
  **byte-exact**, which does not move (305 either way).
- The whole-set check is the specified next step: run the audit over every matched function whose
  winning source includes a `game/**` header, and see how many are affected.

Recorded here because a tier rule that classifies by *how an attempt was labelled* rather than by *what
the source rests on* is a measurement gap, and the two numbers above should not be quoted
interchangeably in the meantime.

## Files

| path | what |
|---|---|
| `eval/type_name_provenance.py` | routes every never-attempted draft's type names to header / `.c` / nowhere |
| `eval/match_claim_audit.py` | audits a claimed match for project-source-backed types; `--types` classifies names |
| `eval/header_admission.py` | `declaring_header` spelling fix; `--only-resolvable` |
| `tests/test_declaring_header.py` | 7 tests, four spellings that must fire, two shapes that must not |
| `eval/results/type-name-provenance-20260917-v2.json` | the routing table |
| `eval/results/match-claim-audit-20260917.json` | the 22 header-less names, each traced to a file |

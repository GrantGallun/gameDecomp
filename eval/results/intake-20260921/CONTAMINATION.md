# The frozen frame's starting drafts carry names from the target's own source

**98 of the 200 frozen intake drafts (49%) use a TYPE or FIELD name that exists only in the target
repo's `src/*.c`** — not in any header (SDK `include/PR/**` or reconstructed `include/game/**`), and
not in the function's own assembly. CLAUDE.md: *ground truth is for CHECKING the miner, never for
feeding it.* Census: `contamination-census.json`.

## How it was found

Reading the residual behind `undeclared-identifier`: m2c's own temporaries looked undeclared.
`clearRaceReplayCourseGrid`'s candidate declares `CourseGridEntry *var_v0;`, but `CourseGridEntry` is
unknown, so clang parses the line as an EXPRESSION (`CourseGridEntry` times `var_v0`) and every use of
`var_v0` cascades into another error — one unknown type became five errors.

`CourseGridEntry` as a type is defined only in `src/race/flow/race_flow.c`. The name is in the frozen
draft and in no m2c context file (`ctx.c`, `ctx.c.m2c`: 0 hits). Fresh assembly-only m2c on the same
function:

| | assembly-only m2c | frozen `base.c` |
|---|---|---|
| declaration | `s16 *var_v0;` | `CourseGridEntry *var_v0;` |
| load | `*(&D_800DC490 + (gRaceCourseIndex * 4))` | `D_800DC490[gRaceCourseIndex.signedValue]` |
| use | `*var_v0 != -2` | `var_v0->status != -2` |

## What the census counts, and what it deliberately does not

A name counts only if it appears in `src/*.c`, in NO header, and NOT in the function's own `.s` file.
The last condition matters: global and function names (`gMenuFlowState`, `enqueueSoundEffect`) are
named by the assembly's own relocations and `jal` targets, so they are binary evidence. m2c's own
vocabulary — `var_*`, `temp_*`, `sp18`, `unk*`, `D_<hex>` — is excluded because m2c generates it from
registers and addresses; it only appears in `src/` because the reference kept many of those names.

| | drafts |
|---|---:|
| carrying a reference-only TYPE (`RaceCourseSurface`, `CourseGridEntry`, `RelocatableHeapBlock`, …) | 41 |
| carrying a reference-only FIELD (`matrixDirty` ×11, `payload`, `palettes`, `counters`, …) | 77 |
| **either** | **98 of 200** |

Two cruder passes were run first and are NOT the result: one counted global symbols (143 — inflated by
names the assembly legitimately carries), and one dropped the m2c-vocabulary filter (190 — inflated by
register temporaries).

## Where the drafts come from

`solver/workspace.m2c_draft` returns an existing `nonmatchings/<fn>/base.c` in preference to drafting,
and falls back to m2c only when that file is empty or failed. `nonmatchings/` is the TARGET repo's
directory — gitignored there, with a `remove temp files` commit in its history.

| | workspaces |
|---|---:|
| total | 2,125 |
| carrying this project's own draft receipt (`target-resolution.json`, `"draft_context": "assembly only"`) | **3** |
| carrying m2c's banner comment, which this project's draft path does not emit | 2,056 |

So nearly every draft this pipeline starts from was produced by the target repo's own tooling, which
evidently runs m2c with its real source as context, and the pipeline adopts it silently.

## What it did and did not do to this session's numbers

| | gained this session | on a contaminated draft |
|---|---:|---:|
| IDO compiled | 15 | 2 |
| IDO + frontend | 15 | 2 |
| byte-exact | 1 | 0 |

The two are `drawMainMenuModeDescriptionPanel` (`selectedTile`, `tileList`, …) and
`renderPickupShardParticle` (`spawnOffsetIndex`, `transformDirty`). The one new exact match,
`writebackMenuRenderScratchBuffer`, is on a clean draft — it is header-assisted for a different reason.

The contamination sits mostly in states that still FAIL: 52% of non-compiling drafts carry it against 34%
of those that compiled at the start. Reference TYPE names arrive without their definitions, so they
cause errors — the `CourseGridEntry` cascade — rather than inflating gains. That is not a reason to
tolerate it: field names like `->status` can still steer a repair toward the reference's layout.

## Scope beyond this frame: the headline number

Run against the project's own byte-exact matches (KB `attempts`, `exact = 1`, recovered strategies
excluded), counting only a TYPE that the target's `src/*.c` defines and no header makes public:

| | |
|---|---:|
| types defined only in reference `src/` | 226 |
| SOLVED winning sources using one (first exact source per function) | 32 |
| functions where **every** exact source uses one | **25** |

`eval.status` now prints the conservative 25 as its own tier, exactly as `header-assisted` was added on
2026-09-01:

| | before | after |
|---|---:|---:|
| functions byte-exact | 347 | 347 |
| — of which SOLVED | 281 | **256** |
| — of which reference-type-assisted | — | **25** |

**No match is lost**; the byte-exact total is unchanged. What changes is the tier they are credited to,
which the ratchet does not cover and which CLAUDE.md says SOLVED must not include. A function lands in
the tier only if EVERY non-recovered exact source for it uses such a type; one clean source keeps it
SOLVED. Examples: `lockRelocatableHeapBlock` (`RelocatableHeapBlock`), `hasPendingRaceReplayCourseGridEntry`
(`CourseGridEntry`), `updateTimeTrialRecordDeltaPopupSlideIn` (`RaceUiPopupActor`).

**Three counts were produced, and two of them were wrong.** A census of every reference-only
identifier gave 198 of 302 — dominated by tool-generated names (decomp-permuter's `new_var`, this
project's own typedecl `padNN`, generic words like `func` and `item`). The first version of the status
tier gave 77 — it recognised two of the three ways a header makes a type public and missed
`typedef struct X X;`. The careful count is 25.

**It is a lower bound.** Field names are excluded because that signal is too noisy to count honestly,
and a source can be assisted by header-KNOWN types that m2c got right only because it was given the
reference as context, which no name-based test can see. The intake frame shows how large that gap can
be: 39 states compiled only from the seeded draft while the name census flagged 13.

Callers of `status.counts(db)` that pass no build tree are unchanged (the tier reads 0), so ratchet
comparisons inside `cohort_reconcile`, `admission_sweep` and the rest stay internally consistent.

## MEASURED: the same 200 functions from assembly-only drafts

`wide-intake-clean.json`, identical pipeline, `GAMEDECOMP_ASSEMBLY_ONLY_DRAFTS=1`:

| | reference-seeded drafts | assembly-only drafts |
|---|---:|---:|
| IDO compiled | 53 | **16** |
| IDO + frontend | 35 | **12** |
| byte-exact | 3 | **2** |

**The assembly-only row is this frame's honest number.** The seeded row is not a capability figure.

The drop is far larger than the name census predicts: 39 states compile only from the seeded draft,
and the census flags just 13 of them. A census of reference-ONLY names under-counts assistance,
because m2c given the full source context also gets every HEADER-KNOWN type right — a draft can be
fully assisted without using a single name the headers lack. The census is a lower bound.

Two different things are mixed into the gap, and the class histograms separate them:

| class | seeded (states / errors) | assembly-only |
|---|---:|---:|
| `redeclaration/conflict` | 22 / 34 | **144 / 150** |
| `member-on-typed-pointer` | 54 / 467 | **133 / 3047** |
| `incomplete-definition` | 49 / 635 | 6 / 9 |
| `undeclared-identifier` | 56 / 476 | 27 / 134 |
| `undeclared-function` | 31 / 69 | 3 / 3 |
| `unknown-type-name` | 15 / 31 | 0 / 0 |

1. **The contamination was CAUSING part of the residual this loop worked on.** The bottom four classes
   nearly vanish on clean drafts: they were reference type names arriving without their definitions
   (`CourseGridEntry *var_v0;` parsed as an expression). A large share of the day's targets were
   artefacts of the contaminated input.
2. **The pipeline was tuned to the wrong input.** `redeclaration/conflict` goes 22 → 144 states on
   clean drafts. Fresh m2c emits its own stubs — `s16 allocRelocatableHeapBlock(s32, ?);`,
   `extern ? D_800DC490;` — which the seeded drafts never contained, so nothing in the intake route
   was built to reconcile them with the declarations `header_variant` brings in. That is a gap in the
   pipeline, not a property of the problem, and it is the first target on the clean frame.

**Every intake measurement before this one was taken on reference-seeded drafts**, including the
frame's own construction and every iteration in LOOP-1 through LOOP-6. The passes built in those
iterations are still correct and still invent nothing, but the gains they were credited with were
measured on the wrong baseline. From here the loop runs on assembly-only drafts.

## What was changed

`GAMEDECOMP_ASSEMBLY_ONLY_DRAFTS=1` makes `m2c_draft` ignore any `base.c` and draft from `target.s`
alone, returning an empty draft rather than falling back to a possibly contaminated one. It is OPT-IN:
the default path is unchanged, because the campaign and other agents run through this function and
switching the default under them would move every number at once. The clean measurement of the same
200 functions is `wide-intake-clean.json`.

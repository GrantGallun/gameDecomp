# Localized typed edits (`solver/site_edits.py`) on the small near-miss frame

PRE-REGISTRATION, written 2026-09-29 before the first run.

## Question

Does one general operator set (compiler-line localization + a closed typed-edit vocabulary, searched
against the oracle) close residuals that the shape-specific `rewrites.propose` pool leaves with zero
proposals? If yes, the long-term direction is to grow that vocabulary, not to add per-shape generators.

## Frame

Unsolved campaign functions under 256 bytes whose retained best attempt has at most six residual diff
lines, from the live campaign map at commit 34409 (`frame.json`, 77 functions at selection time).
Source is each function's retained attempt from `campaign.sqlite`; no reference source, no model.
Attempts are logged to `/home/grant/decomp/runs/site-edits-20260929/trial.sqlite` (strategy `site-edits:*`), not the campaign ledger.

## Recall check (fifth rule: the pass must fire on its motivating residuals)

Hand fixes certified exact on 2026-09-29 (scratch probe): `updateEndingJamSlideLeftToMarker`,
`updateEndingJamSlideLeftFromFarRight` (literal via lui/ori delta), `enqueueSoundEffect`
(s8 -> u8, two lines), `waitForCourseGateTrigger` (subscript x4, two lines).
`initRaceCameraRotationTransition` reaches exact bytes but its frontend fails, and
`drawRaceUiBoardReversePrompt` needs a header declaration, so neither is expected.

## Predictions

- Recall: at least 3 of the 4 certified hand fixes rediscovered. Fewer means the operator set or the
  localizer is broken, and that is fixed before any yield is read.
- Yield: 8-20 of 77 byte-exact with frontend passing, at <= 48 compiles each. Below 5 means the
  vocabulary is too narrow to be the general direction; say so plainly.
- Declines: every function with zero proposals carries a receipt reason; unmapped attribution will be
  the largest single reason.

## Results

Recorded below after the run.

## Results (2026-09-29, one run, 4 workers, `analyse.py` over `/home/grant/decomp/runs/site-edits-20260929/results.jsonl`)

| | |
|---|---:|
| frame | 77 |
| byte-exact with frontend passing, **site edits** | **4** |
| byte-exact after register handoff (site edits changed nothing; baseline was already register-only) | 2 |
| recall on the 4 certified hand fixes | 3 of 4 |
| functions given >= 1 proposal (the old `rewrites.propose` pool gave 17 of 77) | 67 of 77 |
| site-edit compiles | 1,237 (mean 16.1) |
| register-handoff compiles | 268 |
| harness errors | 0 |

Site-edit exacts and the completing edit, each found in at most 3 compiles:
`updateEndingJamSlideLeftToMarker`, `updateEndingJamSlideLeftFromFarRight` (literal `-0x800001 -> -0x7FFFFF`,
the lui/ori delta), `enqueueSoundEffect` (`s8 -> u8` grouped over three tokens), `clearRaceReplayCourseGrid`
(declaration `D_800DC490: s16 -> s32`). Register handoff exacts: `stepRaceMotionLoopingAnimation`,
`stepRaceMotionLoopingJointAnimation`. Those two are register search's, not this module's.

Against the predictions:

- Recall 3 of 4 met the bar. The miss, `waitForCourseGateTrigger`: the grouped `[(i) * 4]` edit makes the
  instruction stream right (`sll 0x4`) but spends a temporary, leaving 11 register faults the 80-compile
  register handoff did not close. The hand spelling `(u8 *)base + i * 16` is exact, so the operator's C
  spelling matters, not only its value.
- **Yield 4 was below the 8-20 predicted and below the pre-registered bar of 5.** As built, the token
  vocabulary is too narrow to carry the frame on its own. The mechanism itself worked: localization,
  grouping and the gradient all behaved, and 67 of 77 received proposals. The residuals just mostly are
  not token edits.
- Declines: 10 of 77 had zero proposals, all with the receipt reason "sites carry no literal, integer
  type, declared identifier, subscript or cast". None were for unmapped attribution, contrary to the
  prediction.

What the 71 unclosed functions are, by dominant `signals` axis of the retained attempt: structural 25,
register 23, relocation 16, immediate 4, ordering 2, width 1. 31 of them differ in instruction count. The
next vocabulary has to address relocation spelling (address materialisation such as `lui/addiu/sw 0(r)`
against a folded `%lo` store, one family of about 7 functions) and statement or expression shape, not more
token swaps.

The 6 exact sources are only in the trial DB. They are not in the campaign ledger.

## Round 2 PRE-REGISTRATION (written 2026-09-29 before running; same 77-function frame)

Changes, all in `solver/site_edits.py`:
- **Two scores instead of one.** The gradient is (instruction distance, register distance). Instruction
  distance counts unmatched instructions after blanking allocatable register names, and register
  distance counts aligned instructions that differ only in registers. Instructions are settled first.
  This replaces the `signals` axis counts.
- **Spelling family.** (a) `readback`: after `G = t;`, use `(G)` for the first or every later use of `t`.
  This comes from an IDO probe and is catalogued as `store-then-reread-global-keeps-its-address`.
  (b) `&A[e]` becomes the pointer sum `(A + (e))`, or the byte form `((u8 *)(A) + (e) * S)` with S taken
  from the target's shift. (c) `A[e]` becomes `*(A + (e))`. (d) The existing `rewrites.propose` pool,
  kept only where it touches an attributed line.
- The register handoff is unchanged (80 compiles). A handoff exact from a function whose site edits did
  not move the gradient is credited to register search, not to this module.

Predictions:
- Site-edit exacts (alone, or with the handoff after site edits moved the instruction distance): 6-12.
  Round 1 had 4. Round 2 is not better unless it clears 5 again and adds at least one family member.
- Of the 5 `spawnEndingCredits*` functions in the frame, at least 3 reach instruction distance 0. Exact
  depends on the handoff.
- `waitForCourseGateTrigger` becomes exact through the byte-offset form.

## Round 2 results (2026-09-29, `analyse.py` over `results-r2.jsonl`)

| | round 1 | round 2 |
|---|---:|---:|
| site-edit exacts | 4 | **6** |
| recall on the 4 certified hand fixes | 3 | **4** |
| register handoffs / exact from them | 6 / 2 | 15 / 2 (both register search's: gradient unmoved) |
| non-exact with improved gradient | 6 | 11 |
| site-edit compiles (mean) | 1,237 (16.1) | 1,387 (18.0) |

New in round 2: `waitForCourseGateTrigger` (byte-offset spelling `((u8 *)(A) + (i) * 16)`, grouped over
both calls) and `updateRaceCamera` (readback). Completing edit kinds: literal 2, spelling 1, readback 1,
type 1, decl 1.

Against the predictions: 6 site-edit exacts is inside the 6-12 range. 6 of the 7 `spawnEndingCredits*`
functions reached instruction distance 0 (predicted at least 3 of the 5 expected; the frame holds 7), but
none became exact. Every one ended register-only, with 5-9 renames: the target keeps `&global` in `a0`
where the candidate uses `v1` or another register, and the 80-compile register handoff closed none of
them. `waitForCourseGateTrigger` became exact as predicted.

Reading: the two-score gradient moved the bottleneck from instructions to registers. The open problem is
register choice on a correct instruction stream, and on this frame it is shared across a family.

## Round 3 PRE-REGISTRATION (2026-09-29, before running; same frame)

Changes since round 2, all in `solver/site_edits.py`. Every rule came from a compiler probe (standalone
IDO 5.3, `cc -S`, and the 7.1 uopt decompile), not from reference source.
- `copydir`: `t = e; ... G = t;` becomes `G = e; t = G;`, seeing through `p = &G; *p = t`. Catalog
  `copy-from-stored-global-keeps-address-in-a0`.
- `declorder`: swap adjacent local declarations when the residual has frame-slot (sp offset) faults.
- The search keeps a beam of 3 per level instead of a single best-first path.

Hand application of the first two already certified 6 of 7 `spawnEndingCredits*` functions exact, so a
yield that counts them is not independent evidence for those 6. Predictions:
- Site-edit exacts: 11-14 (round 2's 6, plus the 7 credits functions through the search, minus any
  regression the beam or new operators cause).
- No round-2 exact is lost. Losing one is a regression to explain.
- Anything beyond the credits family and round 2's six is the out-of-sample signal. Predicted 0-2.

## Round 3 results (2026-09-29, `analyse.py` over `results-r3.jsonl`)

| | r1 | r2 | r3 |
|---|---:|---:|---:|
| site-edit exacts | 4 | 6 | **13** |
| recall (4 hand fixes) | 3 | 4 | 4 |
| register handoffs / exact (register search's) | 6 / 2 | 15 / 2 | 9 / 2 |
| site-edit compiles | 1,237 | 1,387 | 1,200 |
| harness errors | 0 | 0 | 0 |

All 7 `spawnEndingCredits*` functions are exact: `copydir` completed 6 and `declorder` 1
(TumblingSnowboard). No round-2 exact was lost. Completing edit kinds: copydir 6, literal 2, declorder 1,
spelling 1, readback 1, type 1, decl 1.

Against the predictions: 13 is inside 11-14. No function beyond the credits family and round 2's six
closed (0, inside 0-2 but at the floor). The credits exacts are not independent evidence, because the
rule was found on them. The out-of-sample check is the 13 other functions whose targets keep a global's
address in an argument register (`frame-areg.json`, `results-areg.jsonl`).

## Out-of-sample: argument-register address functions (`frame-areg.json`, 13 functions)

These are the functions outside the frame whose targets keep a global's address in a0-a3. Result: 0 exact.
`copydir` fired on 2 of 13 (`__MusIntRemapPtrBank`: instruction distance 24 -> 23; `initRaceCourseSurfaceData`:
worse) and improved neither usefully. Their instruction distances are 8-172, against 5 for the credits
family, and their attributed sites mostly point elsewhere. Reading: "address in an argument register" is a
symptom with at least two causes. The credits family's cause is copy direction (closed, 7 of 7). In
`initCharacterSelectCourseMenuFrom*` the address is reused inside a loop, so IDO legitimately keeps it in a
register, and a0 is chosen because v0 and v1 are busy. That is ordinary register pressure behind a large
structural residual, so it cannot be fixed before the structure is. The rule is real and narrow, as
predicted.

## Deployment

Wired into the campaign as amendment `20260929-site-edits` (checkpoint 35025). The 13 trial exacts are
NOT admitted by it: the campaign will rediscover them through the route, with its own receipts.

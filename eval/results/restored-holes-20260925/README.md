# Restored-holes coverage (2026-09-25)

Question: once the generators lost in amend-20260919 are back in the stream, which "build" holes from
`mechanism-roadmap-20260923b` are still uncovered?

Population: the 22 unsolved functions whose Sept 23-24 best residual was build-class holes only (group a, 7) or
build-class holes plus register allocation (group b, 15); starts are those best nodes (`starts.py`). Same harness,
scheduler, depth and budget 32 as `at-inline-20260924/round4.py`, code-v15 (= code-v14 + main's regalloc_search tie
rule). Arms differ only in the stream: `control` = regalloc_mutations; `restored` = stack_layout + address_symbols
first, then the same stream. Both arms then run regalloc_search (budget 300) from their best node.
Experiment DB only (`~/decomp/experiments/restored-holes-{control,restored}`); no ledger was touched.

## Result

| class at start | functions | closed, control | closed, restored |
|---|---:|---:|---:|
| field:register | 15 | 0 | 0 |
| field:offset | 6 | 0 | 2 |
| field:immediate | 5 | 0 | 1 |
| opcode:lh/lw | 1 | 0 | 0 |
| field:symbol | 1 | 0 | 0 |

Exact: control 0, restored 2 (`guMtxIdent`, `initRaceUiSpinHitTransitionEffect`, both `stack_resize_unused` of an
invented frame pad, 4 compiles each, independently reconfirmed). The campaign ledger already held exact attempts
for initRaceUiSpinHitTransitionEffect, so treat these as coverage evidence, not new project matches.

## Holes still open, by owner

- **Register allocation (15 functions).** regalloc_search really searched (56-301 compiles; 9 of the 14 ran
  out of proposals before the 300 budget) and lowered the register gradient in none but renderRaceUiSingleTrailEffect, whose remaining
  2 non-register steps are stack offsets. Two declined almost immediately: `__osPopThread` (1 compile),
  `resolveAssetTableRelativePointer` (4). Restoring the profile does not cover this class on this population.
- **Incoming-argument home slots.** `probeControllerPak`: a1/a2 homed at 0x1c/0x18 instead of 0x20/0x1c with the same
  frame; 19 stack_layout proposals (pads, resizes, decl order), none moved them. Declaration-order controls do not
  reach argument home slots; this is the real stack hole. `finishCurrentRdpTask` improved only 7 -> 6 offsets.
- **Symbol aliasing.** `returnToCourseSelectModeMenu`: the target stores to 31 separately named globals
  (`gCourseSelectIncomingModelState0`, ...), the candidate to one base plus offsets. It needs either a split into the
  named externs or certificate equivalence when the addresses coincide, and address_symbols does neither.
  *Correction (same day):* the certificate already admits it; see the follow-up below. Not a hole.
- **Width.** `packFixedTransformMatrix` (6 x lh/lw): nothing in either stream touched it.
- **Relocation only.** `osSpTaskStartGo`, `rmonPrintf` score 100 with no instruction class. They belong to the
  certificate stages, which this object-exact harness does not run. *Correction:* the harness does run them
  (`workspace.score` records `verification.function_boundary`); both are certified. Not holes.

Files: `freeze.py`, `starts.py`, `run.py`, `launch.sh`, `analyze.py` -> `analysis.json`, `report-{control,restored}.json`.

## Population follow-up: two new families, all 194 unsolved functions

Two holes from the table above turned out to share one cause: the draft has a different set of *named* locals than
the original, and a named local is its own web (its own stack home when it lives across a call, its own place in
operand order).

- `probeControllerPak`: m2c's `OSPfs *temp_a1 = &gControllerPakHandles[arg0];` lives across a call and takes a stack
  home above the compiler temporaries, so every spill slot was 4 bytes low. Inlining it matched. `single_use` had
  declined silently: it only handles primitive locals without initialisers read once.
  -> `regalloc_mutations.pure_local_inlines` (family `pure_inline`).
- `resolveAssetTableRelativePointer`: `addu v0,a0,t6` vs `addu v0,t6,a0`; a commutative source swap did not flip it,
  `s32 off = arg1 & 0xFFFFFF; return arg0 + off;` did. -> `regalloc_mutations.operand_locals` (family `operand_local`).

Paired arms over the same 194 starts (every function unsolved in the Sept 23-24 worlds), budget 32, no regalloc stage
(`starts_all.py`, `launch_population.sh`, `launch_v17.sh`, `analyze_pop.py` -> `analysis-pop.json`):

| arm | code | object exact (independently reconfirmed) | lost vs previous arm |
|---|---|---:|---:|
| restored | v15 | 3 | - |
| pure_inline | v16 = v15 + pure_local_inlines | 5 (+drawMultiplayerRaceHud 89.5 -> 100, +probeControllerPak) | 0 |
| local_webs | v17 = v16 + operand_locals | 6 (+resolveAssetTableRelativePointer) | 0 |

`field:register` is the class these reach beyond their motivating cases: closed at the end state in 4 -> 7 -> 8 of
156 functions, reduced in 7 -> 14 -> 15. Several of these functions already had exact attempts in the campaign
ledger (reached before the Sept 19 loss), so this measures coverage, not new project matches.

**ROM-certified functions were being counted as holes.** 6-7 functions per arm have a node that
`solver.function_boundary` certifies `function_exact_pending_integration` without object exactness, including
`returnToCourseSelectModeMenu` (97.7; its 31 `field:symbol` steps are aliases of the same addresses) and
`osSpTaskStartGo`/`rmonPrintf`. `eval.mechanism_roadmap.reached` now counts these as reached, not demand.

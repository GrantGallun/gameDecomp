# Chain vs search — results

Pre-registration and amendments 1–3: `PREREGISTRATION.md`. Frame: the 19 greedy-chain functions (`frame.json`), plus
the 77-function regression frame A. Current main-tree generators. No model. Per-arm DBs are in
`/home/grant/decomp/runs/chain-vs-search-20260929/`.

## 19-function frame

| arm | change | exact | compiles |
|---|---|---:|---:|
| default | budget 48, per_step 24 | 0 | 770 |
| class | + class key + focus | 0 | — |
| lane48 | shape edits as a priority lane | 0 | 770 |
| lane16 | lane, budget 48, per_step 16 | **2** | 668 |
| lane72 | lane, budget 72, per_step 24 | **2** | 1012 |

The exacts in both are osMotorStart and osMotorStop, the two the greedy chain found. First run's prediction (both arms reach
them) failed. The trail showed why: budget 48 = 2 × per_step 24, so **depth 3 was unreachable by construction**,
and 20 of level 1's 24 compiles went to typed edits that left the score unchanged. All three lane predictions held.

## Regression frame A (77 small near-misses; 14 exact before)

| arm | exact | lost | compiles |
|---|---:|---|---:|
| lane16 | 13 | clearRaceReplayCourseGrid | 933 |
| **lane72** | **14** | none | 1230 (before: 1290) |

lane16 failed its pre-registered bar, so it was not adopted.

## Adopted (main tree; the frozen campaign is unchanged until an amendment)

- `site_edits.propose`: shape edits (branch_shape, unaligned_copy, temp_copyback, counted_loop) come first.
- Site-edit budget 48 → 72 in `site_edits.search`, `eval/site_edit_repair.run` (default and clamp),
  `completion_campaign` fallback, and `repair_queue` profile. Cost: +31% on the 19-frame, −5% on frame A.
- Tests: `test_shape_repairs_are_a_priority_lane`, `test_default_budget_reaches_the_third_level`.

The search now reaches what the greedy chain reached on the two exacts. For the other 17, lane72's best is at or
above the chain's in 11 and below it in 6: __osContRamRead, __osContRamWrite, __osPfsGetInitData, alFxPull,
osPfsChecker and updateCourseSelectCourseIconList. The largest gap is 5 points (osPfsChecker 72.6 vs 77.6: five
copy-backs in a row, deeper than depth 3).

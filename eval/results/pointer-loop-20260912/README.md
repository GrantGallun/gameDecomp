# One sustained pointer-loop experiment

Selected `drawCharacterSelectCoursePreviewFrame`, 1,224 target bytes, current
attempt 53873 at immutable checkpoint 6136. The initial high-score large-function
shortlist contained scalar drawing counters rather than pointer traversal. This
function was selected for its actual two pointer loops and observed-pass baseline,
with the explicitly lower starting score 83.379. No reference C was read.

## Findings

The target computes a 42-byte row offset for both the tile-map base and corner
lookup. The selected C added `selector * 0x2A` to a `u16 *`, producing an 84-byte
offset. Both byte-addressing with 42 and typed indexing with 21 repair that factor.
The loop itself correctly advances two bytes; changing that increment to `p++`
alone produced exactly the same 83.379 score.

The three added diagnostics use selector 1, selector 9 and forced-selector 9 with
distinct tile halfwords. The original and repaired executions all return normally,
so the results come from differing `drawMenuSpriteTile` argument values, not a
memory fault. Selector 1's first tile is 0x4015 in the target and 0x402a in the original.
Selector 9's first tile is 0x40bd versus 0x417a. The synthetic mapped span deliberately
covers both offsets; it is not a claim about original C object extents or a real
emulator capture.

| Experiment | Compiler similarity score | New diagnostic cases |
|---|---:|---|
| Original candidate | 83.379 | 3 fail |
| Correct byte units only | 82.575 | 3 pass |
| Stack-retention changes only; wrong units retained | 90.699 | 3 fail |
| Correct units + stack/source-shape changes, best round2 | 93.379 | 3 pass |

These are similarity scores, not percentages of proven correctness. The lower
score of the correct unit-only repair and higher score of the still-wrong control
are a concrete reason to retain semantic progress independently from byte score.

The original 64-case normal panel had explicit sprite-index field zero in every
case after resolving overlapping writes. This audit does not infer that every
alternate forced-selector branch was absent. All ordinary panel evaluations
passed, including the original, while the added nonzero-selector/value cases
distinguished the bug. More cases with the same index/content pattern would not
resolve this particular blind spot.

## Experiments and verification

Round 1: 32 variants plus baseline, all 33 compiled and passed the project frontend;
22 score improvements,23 ordinary semantic evaluations. Best 92.644. Factors were
byte/element units, pointer versus index traversal, selector/base-pointer stack
retention, and cursor reuse. Combinations were tested even where their independent
components regressed.

Round 2: 32 variants plus baseline,25 compiled;8 do-while variants were rejected by the
existing workspace build policy before IDO compilation. They remain logged rather
than being hidden or weakening the policy. All33 passed the separate frontend,
and 25 ordinary semantic evaluations passed. Best 93.379. This round tested loop
lowering, the target's unsigned halfword read, and stack-padding placement.

**No candidate is exact.** Best still differs in register allocation, loop-bound
lowering/instruction scheduling, and frame size (0x50 versus target0x58). The best
selector and saved base-pointer stack homes now match 0x4e and0x44. Further padding
alone is not established as the right fix.

There were 66 logged attempts across two native private workspaces,58 successful
builds, no repair-harness model calls, no live attempt-database copy, no canonical
C edit and no campaign import/promotion. The root/agents proposed hypotheses;
this is not an autonomous general-purpose rewrite generator.

## Retained artifacts

* `best-candidate.c`, `best-candidate.diff.txt`, `best-candidate.json`: best source,
  full remaining diff, private attempt and verification bindings.
* `round1-results/` and `round2-results/`: summaries, normal panel records, selected
  source/diff/compiler receipts and artifact hashes. Complete 33-attempt histories
  for each run remain in their native workspace's `history.sqlite`.
* `selector-diagnostics/`: original/best-round1/typed-repair checks, binding checks
  and ordinary-panel selector audit.
* `selector-controls/`: lower-scoring correct fixes and higher-scoring wrong control.
* `selector-round2/`: best and alternate second-round candidate checks.
* `best-binding-verification.json`: all 13 retained compiler artifacts rechecked
  against hashes, with source/panel/case identity.
* `selection/`: immutable checkpoint/source/target/diff bindings and candidate
  selection rationale. `discussion-notes.md`: relevant Discord ideas and limits.

Native workspaces:
`/home/grant/decomp/pointer-loop-20260912/drawCharacterSelectCoursePreviewFrame-1789251567269010535`
and
`/home/grant/decomp/pointer-loop-20260912/drawCharacterSelectCoursePreviewFrame-1789251729539088696`.

The most actionable follow-up is target-guided address-index diversity in normal
semantic panels and source-bound address-unit repair proposals. Existing switch
and register analysis could also expose compact dispatch tables and producer
lifetimes more directly to active repair prompts. The Discord inline-budget claim
remains unverified for this project's compiler and recipe.

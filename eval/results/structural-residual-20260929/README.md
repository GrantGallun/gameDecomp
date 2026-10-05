# What "structural" contains, and which mechanism owns each part

2026-09-29. Read-only over `campaign.sqlite` and `kb-sbk1.sqlite`. The population is every function
unsolved in both ledgers, taken at its best compiling non-exact attempt. Those attempts include
header-assisted sources, so these residuals are measured *after* assistance.

## 1. `signals.structural` was a catch-all (`structural2.py`)

`solver.signals.analyse` bills as structural any aligned difference that isn't a register,
offset/width, relocation, constant or verbatim move. That means: differing opcodes facing each
other, the same branch with a different target, and unpaired instructions. Branch targets are
absolute byte offsets, so one insertion shifts every later target. The 729 flagged functions, by
pattern (patterns overlap):

| pattern | fns | small | medium | large+ |
|---|---:|---:|---:|---:|
| instruction count differs | 578 | 100 | 258 | 220 |
| verbatim instructions moved | 429 | 50 | 178 | 201 |
| non-stack load/store added or removed | 408 | 39 | 171 | 198 |
| branch targets shifted, branch count unchanged | 387 | 82 | 211 | 94 |
| register copies (`move`) | 387 | 47 | 155 | 185 |
| stack spill/save traffic | 383 | 40 | 156 | 187 |
| frame size differs | 328 | 24 | 124 | 180 |
| delay-slot `nop` | 296 | 24 | 114 | 158 |
| `lui` address materialisation | 293 | 18 | 111 | 164 |
| **branch/jump count or kind** | **267** | 26 | 94 | 147 |
| sign/zero extension | 218 | 19 | 80 | 119 |
| index arithmetic (`mult` vs shifts) | 80 | 1 | 14 | 65 |

## 2. Metric fix: shifted branches are not structural (`metric_fix.py`)

`signals.line_map` rebuilds the target→candidate instruction map from the hunk headers. A branch
whose target maps to the candidate's target is now `branch_shift`, or `regalloc` when only its
condition register differs. Over 842 unsolved compiling functions:

| | old | new |
|---|---:|---:|
| functions with structural > 0 | 725 | 677 |
| structural units | 30,083 | 25,082 |
| functions with a single residual axis | 62 | 91 |

2,887 units became `branch_shift` and 2,114 moved to `regalloc`. 48 functions no longer have any
structural fault.

## 3. Sign/zero extension sites (`width_sites.py`)

321 functions have extension instructions on one side only (a broader match than §1). By site:
local/expression 299 (sole kind in 150), call argument 83, incoming parameter 46, narrow store 38,
callee return 32, own return 5. Argument/parameter/return sites are the sole kind in only 16
functions. The hypothesis that binary-type prototypes (all `s32`) cause this class is **refuted**:
only 17 of these best attempts are binary-type drafts. The owner is local retyping. See
`../width-edits-20260929/`.

## 4. Control flow (`control_flow.py`, `branch_fire.py`, `branch_fire2.py`)

Whole-stream counts, 204 functions:

| signature | fns | small | med | large+ | owner today |
|---|---:|---:|---:|---:|---|
| same count, inverted/different condition kinds | 97 | 14 | 20 | 63 | none |
| target has more `slt*` | 76 | 6 | 12 | 58 | `split_merge` (gated) |
| target has more `b`/`j` | 57 | 9 | 18 | 30 | `select_else`, `empty_then_return` (gated) |
| conditional-branch count differs | 44 | 1 | 18 | 25 | none |
| candidate has more `b`/`j` | 42 | 0 | 13 | 29 | `dup_return_merge` (partly) |
| return count differs | 8 | 5 | 2 | 1 | `dup_return_merge` |
| switch lowered differently | 6 | 0 | 2 | 4 | none |

Switch shapes, the "next action" in CLAUDE.md, are 6 functions.

**Fire test on the best states (no compiles):** a gate matches in 121 functions. Generators emit
variants in 72; the other 49 match a gate and emit nothing, which is a silent decline to explain.
In the 83 functions with no gate, 4 fire. One function (`updateCourseSelectPlayerPanels`) crashed
`at_inline` with `max()` of an empty set, killing every family for it; fixed with a regression test.

**Of the 203 variants emitted, 202 have never been compiled in either ledger** (source sha256
checked against `attempts.source_sha256`; the convention was verified on 600 stored rows). The
mechanism exists and fires. What's missing is that it's never applied to these states.
`solver/family_gates.json` gates no branch-shape family. Why: `branch_history.py`. None of the 75 functions ever had a branch-shape family attempt in
the campaign, and register search (the only caller of `branch_shape.families`) never ran on 58 of them. Their best
attempts come from model repair, compile recovery and fault-directed repair, whose children don't go through
register search. Compiling the variants once: `../branch-routing-20260929/` (52 of 75 improved, 0 broke, 0 exact).

## 5. Would more mature mechanisms close these classes?

Measured on the two most exactly-attackable classes. Each mechanism acts on its own class. None finishes a
function alone, because the classes co-occur (91 of 842 unsolved have a single residual axis):

- width retyping: 106 of 299 reduced extensions, 19 cleared them, 0 exact (`../width-edits-20260929/`)
- branch shape: 52 of 75 improved, 0 exact (`../branch-routing-20260929/`)

The gaps are reach (routing, family ordering), composition and acceptance, not missing rules.

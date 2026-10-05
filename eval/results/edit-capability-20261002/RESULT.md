# Result: the model can make the edit; it cannot find it, and it cannot read values out of assembly

Protocol: `PROTOCOL.md`. 51 planted cases (11 classes, 6 per class except where IDO normalized the edit away),
6 information levels, `gpt-oss:20b`, one sample each, 306 attempts. Rows: `~/decomp/experiments/edit-capability-20261002/`.
`python3 run.py report`, `python3 analyze.py`.

## Matrix (exact / n)

| class | kind | L0 C only | L1 +asm | L2 +diff | L3 +line | L4 +class | S line+class, no asm |
|---|---|---|---|---|---|---|---|
| commute | equiv | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 | 0/1 |
| if_invert | equiv | 0/2 | 1/2 | 0/2 | 2/2 | 1/2 | 2/2 |
| stmt_swap | equiv | 1/6 | 0/6 | 1/6 | 4/6 | 4/6 | 5/6 |
| temp_return | equiv | 1/6 | 1/6 | 0/6 | 0/6 | 5/6 | 6/6 |
| const | semantic | 0/6 | 2/6 | 0/6 | 2/6 | 1/6 | 1/6 |
| arith_op | semantic | 0/6 | 0/6 | 0/6 | 0/6 | 0/6 | 1/6 |
| arg_swap | semantic | 0/6 | 0/6 | 1/6 | 3/6 | 4/6 | 6/6 |
| drop_stmt | semantic | 0/6 | 2/6 | 2/6 | 3/6 | 3/6 | 0/6 |
| cast_width | semantic | 5/6 | 2/6 | 0/6 | 0/6 | 3/6 | 5/6 |
| decl_width | semantic | 4/6 | 1/6 | 0/6 | 0/6 | 1/6 | 6/6 |
| **all** | | **11** | **9** | **4** | **14** | **22** | **32** |

Compile failures by level: 9, 20, 12, 5, 4, 1. Median lines changed (every fix is one line): 5, 10, 5, 5, 5, 3.

## Findings

1. **Localization is the bottleneck.** Adding the planted line (L2 -> L3) took exacts 4 -> 14; adding the class
   took them to 22. With site and class the model executes most edits (S: 32/51).
2. **Assembly in the prompt is net negative wherever the fix is inferable without it.** L4 (site + class + asm +
   diff) 22 vs S (site + class, no asm) 32. Showing raw asm doubles the rewrite size (L1 median 10 lines, 44/50
   rewrites >= 5 lines) and doubles compile failures. Same direction as the 2026-09 typed-context A/B.
3. **Where the answer exists only in the asm, the model mostly cannot read it.** `const` best 2/6, `arith_op` best
   1/6 (and that one at S, i.e. guessed). `drop_stmt` is the one class where asm is used: 3/6 at L3/L4 vs 0/6 at S.
   These are the real capability holes.
4. **Many L0/S successes are tells, not skill.** `cast_width`/`decl_width` 5-6/6 without asm: the planted type
   contradicts its surroundings. `arg_swap` S 6/6: "two args are swapped at line N" leaves one possible edit.
5. **IDO normalizes several "equivalent" edits completely** (planting tally, `plant_tally.json`): commutative operand
   order invisible 36/37, mirrored comparisons 46/46, `return` through a temp 118/124, plain if inversion 53/55.
   Adjacent store order is never normalized (6/6 visible). Do not spend search budget on the first four.

## Bugs found in this harness (silent declines)
- `stmt_swap` planted 0 cases from 312 adjacent simple pairs: the independence test compared identifier sets, so
  every `arg0->a = ..; arg0->b = ..;` pair "conflicted" on `arg0`. Fixed to compare whole lvalues.

## Limits
6 cases per cell, one sample: directions, not rates. Mostly small functions, single site-local planted edits; real
residuals (branch shape, regalloc) are not represented. L3/L4/S use the planted site, i.e. a perfect localizer.

## Follow-up: real localizer and the deterministic tool (`localize.py`, `union.py`)

The planted site replaced by `solver.edit_locality.residual_lines` (compiler line records); `solver.evidence_site`
run with no model. Same 51 cases, same model and sampling.

| class | localizer names site | evidence_site exact | R3 asm+diff+lines | RA annotated diff | RS lines only | RE lines, edit format |
|---|---|---|---|---|---|---|
| commute | 1/1 | 1 | 0/1 | 0/1 | 0/1 | 0/1 |
| if_invert | 2/2 | 0 | 1/2 | 1/2 | 1/2 | 0/2 |
| stmt_swap | 6/6 | 0 | 3/6 | 2/6 | 2/6 | 2/6 |
| temp_return | 5/6 | 0 | 1/6 | 0/6 | 2/6 | 1/6 |
| const | 6/6 | **6** | 1/6 | 1/6 | 0/6 | 0/6 |
| arith_op | 6/6 | 0 | 1/6 | 0/6 | 0/6 | 0/6 |
| arg_swap | 6/6 | 0 | 2/6 | 2/6 | 1/6 | 2/6 |
| drop_stmt | 4/6 | 0 | 1/6 | 1/6 | 0/6 | 0/6 |
| cast_width | 6/6 | 3 | 0/6 | 0/6 | 5/6 | 5/6 |
| decl_width | 0/6 | 3 | 0/6 | 1/6 | 4/6 | 2/6 |
| **all** | **42/51** | **13** | **10** | **8** | **15** | **12** |

- **The localizer is good:** verified attribution on 51/51, names the planted line in 42, median 1 line. Its misses
  are structural and documented: declarations compile to no instruction (decl_width 0/6), and a missing statement
  leaves no candidate instruction to attribute (2 drop_stmt cases name nothing).
- **The tool owns constants:** evidence_site is exact on const 6/6, plus half of cast/decl width. Do not train for these.
- **Real vs perfect localization:** R3 10 vs planted L3 14. **The class hint is worth more than the line:** RS (real
  line, no class) 15 vs S (planted line + class) 32. A residual classifier is the larger missing piece.
- **The edit-format prediction failed:** RE 12 vs RS 15. Asking for line edits did not help at this n.
- **Combined:** tool alone 13/51; tool + RS 23; tool + any of the four model prompts 35 (that is four draws, so
  read it as a ceiling). Unsolved by everything with real information: arith_op 5, drop_stmt 4, temp_return 3,
  arg_swap 2, decl_width 1, stmt_swap 1.

## Follow-up 2: full deterministic search, and an operator family (`enum_search.py`, `compare_search.py`)

`solver.site_edits.search` (budget 72, mined lane on, no model) on the 51 cases: **34/51** before any change, far
above evidence_site alone (13). It already solves arg_swap 6/6 (pool `swap args`, mined `( N0 , N1 ) -> ( N1 , N0 )`),
stmt_swap 6/6 (pool `move statement`), const 6/6, cast_width 6/6, decl_width 5/6; arith_op only 2/6, both through an
equivalent literal (`< 0x21` == `<= 0x20`).

Added `_operator_edits` (opt-in `operators=True`): **38/51**, 0 lost, the 4 gains exactly the operator flips.
Argument/statement swap families were also built, measured redundant, and removed.

**Left for anything else: drop_stmt 0/6, temp_return 4/6 unsolved, if_invert 1/2, decl_width 1/6, commute 1/1.**
The model is needed for missing statements; the rest are shape cases.

## Follow-up 3: closing the gaps, scored on a frozen held-out set (`system.py`, `HELDOUT.md`)

Held-out: 42 cases planted on 121 functions the development set never used, frozen (sha256 `850bf1ad…`) before any
of this was built. Development used only `cases.jsonl`.

Built (all opt-in, see PIPELINE_MAP): `solver/missing_store.py`, `solver/next_use_temp.py`,
`rewrite_library.empty_arm_drops`, commutative swaps at attributed lines, `site_edits.widening_hint`, and the
`solver/missing_statement_llm.py` model lane. System = `site_edits.search(operators, gaps)`, then the model lane for
residuals with target-only instructions.

| | development (51) | **held-out (42)** |
|---|---|---|
| site_edits before this session's families | 34 | — |
| + operators (pre-development held-out baseline) | 38 | 35 |
| + gap lane + model lane (code pinned in `system_v2.sha256`) | **51** | **41** |

Held-out per class: every class 6/6 except drop_stmt 5/6 (3 by `missing_store`, 2 by the model lane). The one
unsolved held-out case was not inspected. Bugs found by fire tests on development cases, before held-out scoring:
the widening hint was applied before edit grouping (and missed `(xN)` labels); a held-out run started on that code
was killed unread.

## Follow-up 4: the logic behind the fixes (`eval/rule_probes.py`, `diagnose.py`, `compare_rx.py`)

Fixes were generators; the logic is WHEN a spelling difference survives IDO. Probed on fresh minimal constructs
with the game's -O2 and -O1 recipes (`eval/results/rule-probes-20261002/probes.json`):

| rule | -O2 | -O1 |
|---|---|---|
| `t = E; return t;` vs `return E;` | same 4/4 | differ 4/4 |
| commutative operands | differ for two loads (LOAD order), two params, `*`; same field+param, `x & const` | same as -O2 |
| mirrored comparison | same 3/3 | differ field-vs-field, param-vs-param |
| adjacent store order | differ 4/4 | differ 4/4 (read-modify-write pairs: registers only) |
| empty then-arm | differ only with nested if + code after | differ only when the arm returns a computed value + code after |

Planted cases had suggested wrong rules twice ("IDO does not fold empty arms": it folds them in 9 of 13 contexts;
"store order is directly visible": for read-modify-write pairs only the registers change). Eight catalog entries
record the measured conditions (patterns/catalog.py, 2026-10-02 block, incl. `load-opcode-names-the-access-type`,
previously only in evidence_site's docstring). `solver.principles.residual_rules` names them from a diff:
on development cases the expected rule is named in 31 of the 38 where one applies, 1 knock-on extra, 0 claims
of the undetectable empty-arm rule. Two silent weaknesses found on the way and fixed in `site_edits.widening_hint`
(set-based counting cancelled a candidate-only extension; non-adjacent sll/sra pairs missed).

Model with the diagnosed rule (RX) vs localized lines only (RS), paired on the 25 cases where a rule fires:
**RS 11, RX 15** (7 gained, 3 lost; compile failures 5 -> 2); the planted class hint (S) gave 18. Gains are where
the rule says concretely what the C looks like (temp_return 1 -> 5, stmt_swap 2 -> 4); the losses were cast_width,
whose rule text then said "declaration" where the cause was a cast (wording fixed, not re-run). One sample per
cell: suggestive, not significant (sign test 7 vs 3).

Training records that carry the logic: `export_rationale.py` writes {function + diff, rationale = diagnosed rules
(observed, means), verified edit}; 25/51 development cases have a rule-based rationale, 26 an empty one (no rule
yet: constants, operators, argument order). Held-out is refused by the exporter.

### Rule induction: can the model surmise the compiler's rule itself? (`eval/rule_induction.py`)

The model gets one development case per topic plus a probe tool (it writes two minimal C variants, the real recipe
compiles both, it sees same/differ), up to 8 probes, then writes a rule. The rule is graded by how well the model
then predicts same/differ on unseen validation pairs from `eval/rule_probes.py` families. Arms: no rule (prior), its
own induced rule (2 seeds), the catalog entry. gpt-oss:20b, 10 runs:

| topic | n | prior | induced (seed 1, 2) | catalog | probes distinct/used |
|---|---|---|---|---|---|
| result_temporary | 8 | 8 | 8, 7 | 8 | 1/2, 4/4 |
| commutative_operands | 14 | 4 | 6, 7 | 4 | 6/8, 0/0 |
| store_order | 8 | 4 | 3, 8 | 8 | 0/0, 2/2 |
| empty_arm | 22 | 11 | 19, 11 | 8 | 6/8, 6/7 |
| operator_spellings | 20 | 16 | 16, 16 | - | 5/6, 5/6 |

Prior 43/72 (60%); induced 101/144 (70%); catalog 28/52. 2 of 10 runs wrote a rule without probing.

**CORRECTED 2026-10-03 (external audit, `docs/model-capability-training-audit-20261003.md`, verified here).** This
section first called empty_arm seed 1 (19/22) evidence that probing teaches the rule. All 8 of that run's probes
failed to compile (it used the game function's own names, `arg0`/`gRaceUpdatePaused`, absent from the probe
environment), as did all 8 of commutative_operands seed 1; 17 of 43 probe events failed overall. The run whose
probes succeeded (empty_arm seed 2, 6 informative) scored 11/22, the prior. Always answering "same" scores 18/22 on
empty_arm, and per-topic majority labels 112/144 overall, so raw accuracy here is dominated by label imbalance.
What the data supports: none of the gain can be attributed to successful experiments. The tool returned a bare
`does-not-compile` with no compiler message (a silent decline of our own); it now returns the compiler's error and
refuses non-brace bodies. A valid re-run needs class-balanced validation pairs, attempted/compiled/informative
probe counts, and an arm that writes a rule without probe results.

## Follow-up 5: coverage as the metric, learned search pruning, and a public training corpus

Metric: `eval/coverage.py` (coverage of a frozen panel, coverage at a compile budget, paired verdict: any lost case
refuses). The single-edit panels are saturated, so `plant_multi.py` planted 2-3 visible edits per function on 95
unused functions: train 96 / dev 37 / held-out 54 by function (`HELDOUT_MULTI.md`). `trails.py` collected 4,152
search events on train; `solver/search_priors.py` fits per residual feature which edit family improved the gradient.
The hand-set order spent its compiles on the least productive families (mined 56/1,111, decl 15/1,008; pool
immediate 55/59, uncast 69/113).

Dev (37 multi-edit cases, no model):

| arm | coverage | vs base width 24 | compiles on cases both cover |
|---|---|---|---|
| base, width 24 (today) | 23 | - | - |
| learned order, width 24 | 24 | +2 -1 (refused) | 807 -> 637 |
| base, width 8 | 10 | | |
| learned, width 8 | 20 | +1 -4 (refused); vs base width 8: +10 -0 | 708 -> 232 |
| learned + drop dead families, width 8 | 20 | same as learned 8 | |
| **tiered** (learned, tier 8, a miss pulls the next tier; budget 72) | **25** | **+2 -0 (accepted)** | **868 -> 329** |
| base order + tiers | 24 | +2 -1 (refused) | 852 -> 505 |

The tiered arm is the user's "prune, and regenerate the full set when I need it" at the search level: easy residuals
pay one narrow tier per level, a tier that improves nothing escalates. Held-out scored once, after this table
(`HELDOUT_MULTI.md`, amended before scoring).

Training supply: `public_plant.py` plants the same edit classes inside public decomp translation units (SM64, MK64,
DKR; 3,001 functions already guarded against SBK1/SBK2), recompiling the whole file with its recorded recipe, which
removes the `target_not_self_contained` wall (27 of 240 accepted before). 10,696 verified tasks in under 10 CPU
minutes: 9,673 single-edit (drop_stmt 2,547, arith_op 1,735, const 1,560, arg_swap 1,132, decl_width 1,020,
stmt_swap 823, if_invert 266, commute 211, cast_width 170, temp_return 124, cmp_mirror 85) + 1,023 multi-edit;
file-grouped split train 9,796 / dev 900; sm64 4,560, dkr 4,198, mk64 1,938; ~16.5M tokens.

## What this says to do
(Revised after the follow-up.)
- **Route by residual class:** evidence_site first (constants, widths); the model only for what it cannot state.
- **Build a residual classifier** before training an editor: the class hint is worth +17 exacts, the line +4.
- **Enumerate, don't prompt,** at a localized line. Done for operators (follow-up 2); arg/statement swaps were
  already enumerated. Next: run `operators=True` on real near-miss residuals before making it a default.
- **Post-training target = what neither the tool nor enumeration can state:** missing statements (drop_stmt; the
  localizer names nothing, so the model must read the asm) and the temp/shape cases. Tell cases excluded, held out
  by function, labels re-verified with `byte_certificate.certify` (this harness compares masked dumps), graded on
  real near-misses as well as planted ones.
- Edit-format output is not supported by this data (RE 12 vs RS 15); keep whole-function output for now.

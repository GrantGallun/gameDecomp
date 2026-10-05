# Multiple verified children, and the deterministic machinery that dominates them

**Date:** 2026-09-20
**Scope:** `eval/repair_states.py`, `eval/repair_archive.py`, `eval/target_augment.py`,
`eval/deterministic_repair.py`, `eval/train_source_repair.py` (`--variants --balanced-weights`).
**Assessment written first:** `ASSESSMENT.md` — its predictions are checkable against what follows.

## Headline: the frozen panel is 100% mechanically invertible

| arm | certified matches on the 27 frozen test tasks | model calls |
|---|---|---|
| baseline model, best-of-2 | 4 | 54 |
| trained adapter, best-of-2 | 19 | 54 |
| **deterministic inverse repair + enumeration** | **27 / 27** | **0** |

`eval/deterministic_repair.py` inverts the curriculum's own mutations mechanically, using only the
candidate text the solver was already given. It never reads `generator_source` to build a repair, and
never reads the target assembly either; the recorded answer is consulted afterwards only to report
coincidence. Across the whole corpus it solves **52 of 56** tasks (test 27/27, train 25/29).

| mutation | test | train | also reproduced the answer |
|---|---|---|---|
| `narrow-locals` | 16 / 16 | 8 / 8 | all |
| `split-initialiser` | **8 / 8** | **8 / 8** | all |
| `subtract-to-narrow` | 2 / 2 | 9 / 9 | **none** |
| `while-form` | 1 / 1 | — | yes |
| `drop-switch-default` | — | **0 / 4** | — |

**This is the result that matters, and it is deflationary.** The held-out panel contains no instance
of the one mutation that is not invertible (`drop-switch-default`, 4 tasks, all in train), so the
frozen panel is **entirely solvable by deterministic inversion at zero inference cost**. The
decomposition says the rest:

| | tasks | meaning |
|---|---|---|
| solved by **both** | 19 | every adapter success is inside the free pass's reach |
| solved **only** deterministically | 8 | free matches the model misses |
| solved **only** by the model | **0** | the model adds nothing this pass cannot reach |
| solved by **neither** | 0 | — |

So the adapter's 19/27 is a strict subset of what a zero-model-call pass achieves, and the headline
`4 → 19` measures the curriculum's invertibility rather than repair capability.

**How this number moved, recorded because it is the point.** The first version of this file reported
18/27 and called the residue an honest limit ("transfers to the real corpus not at all"). That was
wrong, and the limit was this work rather than the approach:

- 18 → 19: my `while-form` inverse compared the last line of a **non-greedy body capture** against the
  step, so it declined on every real candidate. The regex already anchored the step.
- 19 → **27**: the catalogue was **source-text-only** and ignored the structure the mutation leaves
  behind. `split-initialiser` re-binds both the assignment target *and* the operand, so no single
  rewrite undoes it -- but the **operator and constant survive**, which makes the plausible
  reconstructions enumerable. `restore-lost-assignment` emits every `<t> = <o> <op> <k>;` over the
  names the function declares or receives (16 candidates per task) and lets the **certificate**
  choose. That alone took `split-initialiser` from 0/8 to 8/8.

The lesson is the project's own: a pass that returns nothing looks exactly like a pass with nothing
to do. Mine declined on the one family that mattered and I reported the decline as a property of the
corpus rather than as a gap in the catalogue.

**What survives.** The `subtract-to-narrow` row: certified 11/11 while reproducing the recorded source
**0/11**. A mechanical pass also lands on different members of the equivalence class -- the lossiness
the certified run showed, from a second direction. And `drop-switch-default` is genuinely not
invertible from the candidate, because the removed arm's return value is absent; it needs the target
assembly, which nothing here consults yet.

## Wiring: the pre-pass is a real stage, not a projection

`eval/evaluate_source_repair.py --deterministic-prepair` runs the inverse catalogue before the draws
and records a certified result as an extra draw (`draw: -1`, `origin: deterministic`). Two properties
are deliberate:

- **The model draw count is NOT reduced**, so an arm with the pre-pass is compared to one without at
  the same inference budget. The pre-pass can add matches and can never trade a model draw away.
- **The two purchases are reported separately** — `tasks_exact_deterministic`,
  `tasks_exact_by_model`, `tasks_exact_by_both` — because summing them would let a free inversion be
  read as model capability. `tests/test_deterministic_repair.py` pins that.

## A defect in the curriculum, found by trying to invert it

`split-initialiser` solved 0/8, and the reason is not the inverse. Its candidate reads:

```c
    s32 var1;    s32 tmp_var1;

    tmp_var1 = var0 * 9;
    var1 = tmp_var1;
```

where the answer reads `var0 = arg0 * 9;`. In `eval/repair_mutations.split_initialiser`,
`_MUL_DECL`'s **group(1) is the declared variable** (`var1`) while group(2) is the assignment target
(`var0`), and the emitted code uses group(1) for both:

```python
var, param, k = match.group(1), match.group(2), match.group(4)
extra = f"    s32 tmp_{var};\n\n    tmp_{var} = {param} * {k};\n    {var} = tmp_{var};"
```

So the assignment `var0 = arg0 * 9;` is **replaced** by `var1 = tmp_var1;`, `var0` is left
uninitialised, and the damaged source means something different from the answer. The docstring says
the mutation adds "an intermediate local [which] changes which values live in which registers" —
semantics-preserving. It is not. Group(2) is the correct variable to use.

**This is a report, not a fix.** Correcting it changes the mutation, which changes the dataset, which
invalidates the frozen manifest and every number measured on it — including the 4 → 19 headline. It
also explains why `split-initialiser` is the family the adapter fails (3/8): it is the one family
where information is destroyed rather than reshaped.

## The multiple-children layer (built, tested, not yet informative)

| piece | state |
|---|---|
| repair STATE identity | `repair_states.state_id` hashes target + parent + feedback + build + prompt; `assert_states_are_not_functions` refuses a grouping collapsed to ids |
| harvest | `target_augment.py`: 82 certificate-verified variants over 29 train tasks; refuses any split but `train` |
| balanced weighting | function → state → novelty class; 29 train records over **17 functions** and 29 states |
| training examples | 29 → **82** (111 before dedup); **29 collapsed** as cosmetic |
| archive | `repair_archive.py`, bounded, deterministic, never computes exactness |
| tests | `tests/test_repair_states.py` + `tests/test_repair_archive.py` — **47 passed** |

**It is not yet informative about the objective, and the reason is measured:** after deduplication the
82 children are 29 `typedef-spelling` + 21 `explicit-s32-casts` + 3 `hoist-default` + 29 recorded
children. Only `hoist-default` (3 of 29 tasks) changes the program's *structure*; the rest are
spellings a lexical descriptor cannot distinguish from a real difference without a typedef map. So
arm B reweights near-identical text for 26 of 29 states, and arm C's archive is inert because the
synthetic evaluator draws independent samples (`repair_passes: 0`) — there is no seed choice for an
archive to influence. Both were predicted in `ASSESSMENT.md` §1 and §2 before the pilot.

## Bugs in this work, found by verification rather than review

1. **`canonical` did not strip comments**, so `cosmetic_duplicates_collapsed` measured **0 of 82** —
   every comment-only variant counted as a distinct child and inflated both diversity and weight.
   Fixed: 29 now collapse.
2. **`canonical` normalised whitespace runs but not punctuation spacing**, so `return 1 ;` differed
   from `return 1;` and a cosmetic reflow counted as a second approach. Fixed.
3. **Collapsing duplicates in the weight map did not remove them from the example list**, so a
   comment-only variant still trained at its class's weight — double-counting. `load_task_examples`
   now keeps one example per novelty class: 111 → 82.
4. `s32`/`u32` would have been alpha-renamed into the same placeholder, hiding a signedness change
   that alters codegen. Fixed by protecting the project's fixed-width type names.
5. `repair_archive`'s first draft would have collapsed nothing at all for the same whitespace reason;
   caught by the test for the motivating case before it reached the pipeline.

## Recommendation on trajectory-aware RL

**Not on this panel, under any training regime.** It is 27/27 mechanically invertible at zero
inference, so no learned policy can demonstrate anything on it, and the 4→19 headline measures the
curriculum's invertibility rather than repair. Trajectory-aware RL also needs a search whose
*allocation* changes the outcome, and the research-policy replay already showed every sane policy
reaching regret 0.000 at budget 48 (`GATE-REVIEW.md`).

The precondition is a task family where the defect is **not known by construction and not
enumerable** — the real game corpus, where the residual must be read from the compiler's diff. That
is `solver/diffrepair.py`'s territory, and it is now demonstrably the right next thing to measure:
this pass never consulted the target assembly, and the one mutation it cannot invert
(`drop-switch-default`) is exactly the one that would need it.

## Reproduce

```bash
bash .cache/recon/test_multichild.sh        # 47 tests: archive + states + weighting
bash .cache/recon/deterministic_repair.sh   # the 18/27 table, both splits, zero model calls
bash .cache/recon/dry_abc.sh                # arm A vs arm B example/weight counts, no GPU
bash .cache/recon/augment_targets.sh        # harvest the verified variants
```

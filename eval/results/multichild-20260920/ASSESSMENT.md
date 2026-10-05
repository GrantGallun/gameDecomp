# Preserving multiple valid decompilations: what is sound, what needs changing

**Date:** 2026-09-20
**Status:** assessment written before the pilot, so its predictions can be checked against it rather
than fitted to it.

## What is sound, and now measured rather than assumed

**"A recorded successful child is not necessarily the only valid answer"** — correct, and the size of
the effect is already known from the certified run (`evaluation-certified/ATTEMPTS.md`). Of its 32
exact draws, 22 are the recorded answer modulo whitespace and **10 are substantively different
programs producing the identical object** — one replaced `extern s32 syn_ext(s32);` with
`typedef`/`s32_t` spellings, another moved a trailing `return 0;` into a `default:` inside the
switch. The oracle accepts all of them, so a pipeline that keys on the recorded child is discarding
verified positives.

**The state must be the conditioning tuple, not the function id** — correct, and this is the failure
mode most likely to arrive silently. A loader that grouped by `function` would attach a child to a
parent it was never verified against and would look healthy from the outside. `repair_states.state_id`
hashes target + parent + feedback + build + prompt, and `assert_states_are_not_functions` refuses a
grouping that collapsed to ids.

**A similarity drop is not a failure** — correct. The repository's one negative result here
(`sequential-diff-refinement`, REFUTED: 8 functions, zero rescued, best-of-N 6/8 vs 3/8) was measured
on **raw assembly → C with an instruction diff as feedback**. That is a different regime from this
one, where the model is handed a compiling candidate and the compiler's own output. So the earlier
refutation does not transfer, and the brief is right to keep the two apart.

**Do not invent credit from ancestry** — correct and cheap to respect: `trajectory_labels` reports
that a route *reached* a certified match, never that every edit on it was necessary, and never
relabels an ancestor as a solution.

## What needs modification, with reasons

### 1. Scope 4 presupposes a search that branches, and this pipeline has none

"Add an optional bounded archive to the existing repair search" only means something if the search
**chooses seeds**. The synthetic evaluator does not: `evaluate_source_repair.evaluate_arm` draws N
*independent* samples per task and certifies each. `equal_budget_eval.py` states the position
outright — `repair_passes: 0`, "best-of-N is the control, not a treatment".

So an archive bolted onto the current evaluator would change nothing: there is no second round for a
retained candidate to seed. **Arm C is not "B plus an archive"; it is "B plus multi-round attempts
plus an archive."** That is a larger change than the brief implies, and the pilot below is designed
around that fact rather than pretending otherwise.

### 2. Weighting cannot add information that is not in the data, and here there is very little

The conceptual objective `L(x) = -sum_c w_c * log p(c | x)` reweights evidence that already exists.
It cannot make the model explore, and it cannot reach a solution the dataset does not contain. The
brief says not to claim otherwise; the sharper point is that **the lever may be close to inert on
this dataset**, and that is measurable before any GPU time:

- the four rewrites in `eval/target_augment.py` produced 82 accepted variants over 29 train tasks;
- but cosmetic collapse is real — `typedef-spelling` and `blank-line-and-comment` re-spell the same
  program, and under one shared novelty definition they collapse onto the recorded child;
- only `hoist-default` is **structural**, and it fires on 3 of 29 tasks.

So the *novelty-distinct* child count is the number that decides whether arm B can differ from arm A
at all, and it is reported explicitly rather than assumed. If it is ~3, the honest result is "too few
distinct children to test the idea", which the brief explicitly permits and prefers to manufactured
evidence.

### 3. Per-example weights are only exact at batch size 1, and the trainer must refuse otherwise

HuggingFace's loss with `labels` and `-100` masking is a **mean over completion tokens pooled across
the batch**. Two consequences the implementation has to be explicit about:

- a longer completion contributes more gradient, so a verbose spelling of the same repair already
  outweighed a terse one *before* any weighting — the weights do not fix that and are not meant to;
- with `batch_size > 1` a per-example weight has no exact meaning. `train_source_repair` therefore
  **refuses** a weighted run at batch size > 1 rather than approximating silently.

Weights are also scaled so the dataset's *average* weight is 1.0. Without that, weights summing to
1.0 across ~111 examples would divide every gradient by ~111 and the run would look like a much
smaller learning rate — a silent failure that would be read as "weighting did not help".

### 4. Novelty must be defined in exactly one place

The archive uses novelty to decide what to store; the trainer uses it to decide what to weight. If
those two disagree about what counts as cosmetic, the duplicate test becomes vacuous. Both import
`eval.repair_archive.novelty_key`. A descriptor that hides a real difference — a changed constant, a
changed operator, a changed control-flow keyword — is a bug, and each of the three is tested.

### 5. Hindsight relabeling (scope 6) does not fit this objective

Constructing `B' → C` where C compiles to B' and misses B makes C a positive for a **new** target B'.
That is legitimate, but it is not a small isolated extension here: the prompt for B' has to be
regenerated from B''s own assembly, parent candidate and compiler feedback, and B' is an object the
training run never had a target for. It also inverts the incentive — every failed attempt becomes a
new easy task — and the brief itself warns about easy self-generated tasks crowding out real ones.

**Recommendation: document as follow-up, do not implement in this pass.** The evidence needed to
decide later is the same graph `trajectory_labels` already records.

## What would make this fail, stated in advance

1. Too few novelty-distinct children per state for arm B to differ from arm A.
2. Arm C's archive changing nothing because there is no branching search to feed.
3. The gain being concentrated on the compile-error axis, as the certified run was — the baseline's
   failures there are dominated by C89 syntax errors, and nothing about multiple children addresses
   that.

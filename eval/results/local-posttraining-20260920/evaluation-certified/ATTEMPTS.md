# Do attempts buy anything? Two premises measured, and what they change

**Date:** 2026-09-20
**Source artifact:** `evaluation-certified/evaluation.json` (the certified run), plus the frozen panel.
**No compute:** every number below is read out of artifacts already on disk.

The argument being tested: *compilation is lossy, a true decompilation takes several attempts, and
models are somewhat deterministic — so if the attempts are built in, the result should improve.*
Three parts. Two are measurable now and they pull in opposite directions, which is what makes them
worth having.

## 1. How deterministic? More than the base model — by a lot

The run drew twice per task per arm at fixed sampler settings (`temperature 0.8`, `top_p 0.95`,
`seed 20260920`). Draws that repeat their sibling byte for byte are attempts that bought nothing:

| arm | tasks whose two draws are byte-identical |
|---|---|
| baseline | **1 / 27** (3.7%) |
| adapter | **8 / 27** (29.6%) |

**The fine-tune collapsed diversity eightfold.** The baseline explores; the adapter has learned a
preferred answer and repeats it. So for the adapter, roughly **30% of a repeated attempt at the same
conditioning is provably wasted** — and that fraction grows with every additional attempt drawn
identically.

This is the strongest argument for the user's point, and it also sharpens it. It is not that
"attempts" are unavailable; it is that **attempts only pay if they are made to differ.** Resampling
the same prompt is the one way to spend the budget that the adapter is measurably bad at using.

## 2. But resampling does still pay

| | adapter |
|---|---|
| tasks solved on draw 0 | 12 |
| tasks solved on draw 1 | 15 |
| tasks solved on either | **19** |
| bought by best-of-2 over the better single draw | **+4** |

So the 70% of draws that *do* differ carry real information, and the tail is worth reaching for. The
correct reading of 1 + 2 together: **the attempt budget is worth spending, but on diverse attempts
rather than on repetition.**

## 3. Is compilation lossy *in practice* here? Yes — and this is the strongest result

`compilation is lossy` is a claim about the search space: C → object is many-to-one, so the target is
an **equivalence class of sources**, not a source. The certified run can test whether that lossiness
is actually exploited.

First, a field note, because it is an easy mistake: **`text_identical` in this artifact compares CODE
IMAGES, not source text.** Two different programs compiling to the same object both have
`text_identical = True`, so that flag cannot answer this question. The comparison has to be the draw's
**source** against the task's hidden answer.

Every exact draw differs from the hidden answer by digest — 27/27 adapter, 5/5 baseline. A digest
difference alone proves nothing (a trailing newline would do it), so the text was diffed:

| | count |
|---|---|
| exact draws examined | 32 |
| identical once whitespace is normalised | 22 |
| **substantively different programs** | **10** |

The ten are not reflow. They are different programs:

- `syn:stack_spill:5:narrow-locals` — the model **deleted the `extern s32 syn_ext(s32);`
  declaration** and instead wrote `typedef u32 u32_t; typedef s32 s32_t;`, using those spellings
  throughout. Same object.
- `syn:switch_sparse:3:subtract-to-narrow` — the model **restructured the switch**: it added
  `default: return 0;` *inside* the switch and removed the trailing `return 0;` after it, plus an
  explicit `(s32)` cast. The task's own mutation is `drop-switch-default`; the model restored the
  behaviour in a different position. Same object.
- another added type definitions and a comment the answer does not contain.

So **a third of the verified repairs are programs the generator's answer never contained.** That is
direct evidence that the model is *synthesising* rather than recalling — it found other members of
the equivalence class — and it is the reason the certificate is the right oracle and "similarity to
the answer" is the wrong one. A pipeline that scored against the answer text would have thrown these
ten away as failures.

## What this changes about the next experiment

The three findings jointly reorder the work. Attempts are worth building in, but the treatment is
**not** "draw more times":

- Independent resampling at the same conditioning is the *control* (`equal_budget_eval.py` records
  `repair_passes: 0` and states "best-of-N is the control, not a treatment"). It is already the
  thing the certified run used, and it is what the adapter is worst at using, at 29.6% duplication.
- **The treatment is a second attempt conditioned on the first attempt's own candidate and the
  compiler's feedback on it.** That is a different prompt, so it cannot be a duplicate draw, and it
  is the mechanism that has never been measured on this curriculum. The repository's one negative
  result here (`sequential-diff-refinement`, REFUTED) was raw assembly → C with an instruction diff
  as feedback; this setup hands the model a compiling candidate plus compiler output, which is a far
  better-conditioned starting point and a different experiment.
- The outcome must not be "similarity to the answer". Primary stays **object-exactness from the
  certificate**; and the synthesis rate — *exact draws whose source is a substantively different
  program* — becomes a first-class secondary outcome, because it is the metric that separates
  "learned the answer" from "learned to search".

Two design constraints fall straight out of the measurements:

1. **Budget must be matched on tokens, not calls.** A sequential attempt carries a longer prompt
   (the previous candidate and its feedback), so equal-call arms are not equal-compute. Both arms
   must be matched on total tokens, with prompt and completion reported separately.
2. **Every sequential prompt must pass the existing leakage boundary.** The rendered prompt for
   attempt *k+1* contains the model's own prior output, and the check in
   `frozen_manifest.task_prompt` must run on it exactly as it runs on attempt 0. Skipping it there
   would be a silent hole in the one boundary the audit found missing.

## Reproduction

```bash
python .cache/recon/measure_attempts.py           # determinism, source-vs-answer digests, draw positions
python .cache/recon/characterise_divergence.py    # whitespace-normalised diff of every exact draw
```

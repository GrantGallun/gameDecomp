# Frozen splits, panel and metrics (pre-registration, written before arms B and C were run)

Written after arm A (scripted) and before any model arm. The point of writing it now is that the panel
and the metrics cannot be chosen after seeing which policy wins.

## Splits

`eval/results/tool-action-20260921/splits.json`, produced by
`python -m eval.tool_action_dataset --mode splits --functions 60` from the same KB query the earlier
tool-agent work used (functions with zero exact attempts, smallest first).

| bucket | functions | families |
|---|---|---|
| train | 28 | Fcu, Fpa, acquire, al, alloc, get, ldexp, ll, memcpy, release, request, rmon, set, strlen, ull |
| dev | 24 | Fvi, free, init, noop, os, resolve |
| test | 8 | Fdr, Fwa, Mus, add, dispatch, gu, reserve |

Assignment is by FUNCTION FAMILY, never by function, so siblings and their descendants stay in one
bucket. Families already reported on by the earlier tool-agent panel
(`eval/results/tool-agent-20260920/head-to-head.json`) are pinned to dev rather than allowed into
training -- those functions have published numbers and reusing them for training would make the
held-out claim fiction.

Held-out surfaces: the procedural exercises use a different function name, source text, residual text
and tool arguments per split (`SURFACES` in `eval/tool_action_dataset.py`). The RULES are identical
across surfaces; only the surface changes, so a policy that learned a rule transfers and one that
memorised a name does not.

## Environments

1. **Procedural, held out.** 12 exercises per split, graded by the mechanical state rules in
   `eval/tool_action_dataset.label_for`, regenerated from each record's own stored state at grading
   time (`grade()`), so a record cannot validate its own label. No compiler, no model call needed for
   arm A.
2. **Repair panel.** `dev` split, first 6 functions, budget 5 action slots, real m2c drafts, real
   runners, real object certificate. The panel list is fixed here, before arms B and C run.

## Metrics, and what each one can support

| metric | level | what it can support |
|---|---|---|
| parse / validate / invalid proposals | protocol | format competence only |
| acceptable rate vs the mechanical label | procedure | acting on the observation |
| redundant compiles (`compile` while the source already carries a verdict) | procedure | the compile contract |
| repeats after no-effect on an unchanged source | procedure | reading the result |
| missing-prerequisite calls | procedure | reading `needs` |
| premature stops / honest stops | procedure | honest stopping |
| ablation delta (dev, tool results removed) | information use | acting on feedback rather than shape |
| certified matches under the caps | task outcome | game-solving improvement |
| setup + internal compiles, tokens, wall seconds | cost | what the result cost |

## The separation being measured

- **Protocol learning** = valid actions and correct arguments. The base model already achieved 100% on
  12 functions (78 generations, 0 invalid), so a gain here is not the interesting claim.
- **Observation-dependent decision learning** = the procedural metrics, especially the ablation delta
  and the paired cases (`fresh_state` vs `verified_state`, `no_effect_unchanged` vs
  `retry_after_change`). These are checkable without solving anything.
- **Verified game-solving improvement** = `certified_matches` on the panel. A handful of functions at
  budget 5 is a pilot, not a capability claim, and a difference of one here would not be reportable as
  improvement without more functions.

## Arm A result, recorded before B and C (scripted, procedural)

| split | acceptable rate | honest stops | labels reproduced from state |
|---|---|---|---|
| train | 1.0 | 3 | 12/12 |
| dev | 1.0 | 3 | 12/12 |
| test | 1.0 | 3 | 12/12 |

The scripted arm scores 1.0 because it now implements the same mechanical rules (verification state,
no-effect set, declared prerequisites, honest stopping) -- `ScriptedPolicy` reads the observation. That
makes it the PROCEDURAL CEILING on these exercises, not a discriminating baseline: it is the bar a
learned policy has to reach, and it says nothing about game-solving.

## Addendum, after the model arms ran

Two things were added after this pre-registration, and both are recorded here rather than quietly
folded in:

1. **`missing_prerequisite_calls` became a reported metric** (spec §6 names it). It was not in the
   table above because it did not exist when the first arms ran; every arm was re-run so all four carry
   it. Nothing else about the panel, the environments or the grading changed.
2. **A fourth arm exists: C2**, the adapter retrained after the one bounded correction round (spec §5).
   It is measured on exactly the same held-out artefacts as arm C. Adding an arm after seeing results
   is a real risk to the pre-registration, so the correction it embodies was aimed at a mistake
   identified mechanically on TRAIN functions, not at a metric this panel reports, and the direction of
   its result on the panel was not assumed in advance -- it improved one metric and worsened another.

Results: `REPORT.md` §5 and §5b.


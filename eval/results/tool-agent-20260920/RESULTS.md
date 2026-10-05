# The scripted deterministic agent on real game functions: 1 of 25

**Date:** 2026-09-20
**Artifacts:** `transcripts.jsonl` (25 episodes), `scripted-run.json`, `action-space.json`
**Model calls: 0.** Every action is one of the eight declared in `eval/tool_registry.py`.

## Result

| | |
|---|---|
| functions attempted | **25** (unsolved, smallest-first, from the KB's `attempts` table) |
| certified matches | **1** — `allocRenderCallbackScratchBuffer` |
| model calls | **0** |
| wall clock | ~18 s for all 25 |

The match came from a four-step sequence, all deterministic:

```
compile (failed) → invert-mutations (changed) → compile (failed) → resolve-placeholders (exact)
```

`resolve-placeholders` is what closed it: m2c writes `?` where it cannot infer a type and IDO refuses
the whole translation unit for it, so replacing the placeholders turned an uncompilable draft into an
exact one. This is the first new game match produced by this machinery, and it cost compiler calls
only.

## Per-action applicability — the number that matters more than the match

| action | fired | changed the source | closed |
|---|---|---|---|
| `compile` | 25 | — | — |
| `invert-mutations` | 25 | 1 | 0 alone |
| `resolve-placeholders` | 25 | **3** | **1** |
| `diffrepair` | 25 | 0 | 0 |
| `redraft` | 25 | 22 | 0 |
| **`regalloc-search`** | **0** | — | — |
| `uopt-trace` | not in the scripted order | — | — |

**`regalloc-search` never ran.** It reported `not-applicable` on every function because the context
carries no `target_dump`, and it is the intervention with the most evidence behind it in this
codebase — the 39.5% of game residual faults that are allocation rather than structure. The action
space is doing its job by naming the missing input, but the run therefore has a hole where the
single most promising action should be.

`resolve-placeholders` closed 1 of the 3 it changed: a 33% hit rate on the only action that
demonstrably works here, and worth knowing before anything else is built.

`redraft` changed the source on 22 of 25 and closed none. That is the decontaminated m2c draft
(assembly-only, no `--context`), so it is a legitimate starting point, but as a bare transform it
does not move the target — consistent with the 0-of-56 improving-children measurement on this corpus.

`diffrepair` ran on a real diff (the fix: the diff now comes from compiling the candidate under
consideration, not from a KB lookup that returned nothing) and found no layout constraint to apply on
any of the 25. Layout is 2.8% of the fault mass, so this is expected rather than damning.

## What this does and does not establish

**Establishes:** the action space, the dispatch layer, the oracle integration and the transcript
format all work on real game functions. One certified match came out of a fixed-order deterministic
policy with no model. The transcripts are a real dataset: 25 episodes, every action's status recorded,
labelled by the workspace oracle.

**Does not establish:** a bar of any statistical weight. n=1 of 25 with the most promising action
disabled is a plumbing result, not a measurement of what deterministic repair can reach. And these 25
are the KB's *smallest* unsolved functions, so they are not representative of the 261.

## Next, in order

1. **Feed `regalloc-search` its `target_dump`** and re-run. It is the largest hole and the largest
   expected effect; until it runs, no bar can be quoted.
2. **Run the full 261**, not the smallest 25.
3. Then, and only then, does a trained policy have a number to beat.

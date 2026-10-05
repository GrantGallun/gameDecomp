# Model zero-shot policy vs the scripted order: tied on outcome, and the gap is procedure

**Date:** 2026-09-20
**Artifact:** `head-to-head.json` · 12 unsolved game functions · budget 7 action slots per arm ·
same registry, same runners, same oracle, same functions.

## Result

| | scripted | model (zero-shot, no adapter) |
|---|---|---|
| certified matches | **0** | **0** |
| candidates produced | 13 | 15 |
| generations | 0 | 78 |
| invalid proposals | — | **0** |
| wall clock | 9.0 s | **54.6 s** (6×) |

**Neither arm closed anything, and the model did not beat a fixed order.** Its format is solid —
0 invalid proposals in 78 generations, so the prefill fix holds under a closed loop — and it produced
two more candidates than the script, which is a tie in substance.

## What the action sequences show, and it is the useful part

**The model chooses sensibly.** First actions across the 12: `resolve-placeholders` ×6, `compile` ×3,
`redraft` ×1 — it prefers the one action the scripted run found actually closes matches, and it
varies the choice with the function.

**It repeats actions that already failed.** Around **12 of 78 generations (~15%)** re-propose an
action that had just returned `no-change` or `not-applicable`, against an explicit instruction in the
system prompt not to. Examples: `diffrepair` twice on `__osPopThread` (both not-applicable),
`redraft` twice on five separate functions, `resolve-placeholders` twice on four.

**It stops early on 4 of 12** — using 5 of the 7 slots — which is better discipline than the script
in one respect and worse in another, since the script's trailing `compile` is what confirms a
transform's effect.

**It reached for a tool the script never uses**: `uopt-trace` on `Fviboff`. It came back
`not-applicable` (no `source_path` in the context) and was reported as such rather than crashing.

## What this settles

The question was whether the model "doesn't know how to act on the data, and that might be
trainable". Measured:

| capability | status |
|---|---|
| emit a legal action | **solved** by the prefill: 0 → 12/12 in the probe, 0 invalid in 78 live generations |
| choose a sensible action | **already present zero-shot** — comparable to the script, prefers the highest-yield action |
| follow the procedure | **the actual gap** — repeats dead actions, ~15% of slots |

So the trainable target is neither tool syntax nor tool selection from scratch. It is **loop
discipline**: do not re-propose an action that just returned no-change, and compile after a transform
before deciding it failed. That is a small, well-defined, cheaply-taught behaviour, and it is exactly
the kind of thing the 25 transcripts *can* teach — not because they contain good decisions, but
because they contain well-formed sequences that never repeat a dead action.

**Not claimed:** that fixing this closes functions. Neither arm closed any of these 12, so the
per-function ceiling here is zero for both, and procedure training would have to be measured on a
function set where something actually closes.

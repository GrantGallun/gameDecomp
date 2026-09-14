# Autodecomp — Training Path

## The sequencing constraint

You cannot train anything useful until the loop runs, because **the loop is what produces
the dataset**. Public decomp data gives you (asm → matched C) pairs, and the published
result for training on exactly that is ~1.2% byte-exact with fine-tuning barely beating
zero-shot. That road is already mapped and it is a dead end.

What does not exist in any public corpus — and what your loop generates for free — is
**refinement data**: given a candidate C, its compile result, and its instruction diff,
what edit raises the score. Nobody's pretraining set contains that. That is where a
trained model has real headroom.

So: build the loop, log everything, then train on your own trajectories.

---

## Tier 1 — No training (do this first)

**Best-of-N against the verifier.** Sample N candidates, compile all of them, keep the
highest scorer. Because the verifier is perfect and cheap, this is training-equivalent
capability for zero training cost — it is rejection sampling with a ground-truth reward.

Also in this tier, all free:
- Retrieval from the KB into the prompt (known types, sibling functions in the same TU,
  previously matched functions with similar shapes)
- Few-shot examples drawn from already-matched functions in the *same* TU
- Feeding the instruction diff back verbatim rather than summarizing it

**Measure this before considering anything below.** If best-of-20 plus KB retrieval
already clears the Phase 2 gate, training is an optimization, not a requirement.

---

## Tier 2 — SFT on the refinement step

Not on the draft step. The draft step (asm → C) is what has already been shown not to
benefit much. The refinement step is different:

```
input:   current C  +  compile result  +  instruction diff  +  score
output:  revised C  (that scored higher)
```

Mine these from explicit `attempt_edges`: a parent/child pair where the child improved
is a training example. Row order is not lineage — best-anchored refinement can branch
from an older attempt, while best-of-N samples are independent roots. Attempts that
*decreased* the score are negative examples — keep them, they matter for Tier 3.

- LoRA on a 7–14B code model is sufficient; this is a narrow, highly patterned task.
- Expect it to learn compiler-specific idiom repair: IDO register allocation habits,
  branch-shape choices (`for` vs `while` vs `do/while` vs `goto`), signed/unsigned
  slips, `s32` vs `int` typedef choices, temp-variable count and declaration order.
- Those are exactly the things that decide the last 5% of a match and are close to
  invisible semantically.

**Guard:** hold-out discipline is per *function*, not per attempt. All attempts for a
given function go entirely into train or entirely into eval, never split across.

---

## Tier 3 — RL against the compiler

This is the correct end state, and it is what the prior-art author concluded after SFT
underdelivered. The setup is unusually favourable:

- **The reward is dense.** objdiff gives 0–100, not pass/fail. Mainstream decompilation
  RL work has to fall back on coarse "re-executability" because it lacks ground truth.
  You have per-instruction agreement.
- **The reward is free and exact.** No reward model, no LLM judge, no human labels.
- **Reward shaping is natural.** Score delta per edit, with a terminal bonus at 100.

GRPO fits well: sample a group of candidate edits from one state, score all of them,
advantage against the group mean.

**Hard prerequisite: a fast Oracle.** RL calls the reward function constantly, so the
Phase 0 acceptance target (cached score under 50ms) is not cosmetic — it is what makes
Tier 3 tractable at all. If scoring is slow, this tier is closed to you.

---

## The economic argument for a small model

Independent of quality. A run over ~5,000 functions at ~50 iterations each is ~250,000
model calls. At frontier prices that is the dominant project cost.

Target architecture once Tier 2 works:

| Role | Model | When |
|---|---|---|
| Bulk refinement | distilled 7B, fine-tuned on your trajectories | default path, most iterations |
| Escalation | frontier model | on stall, on CHALLENGE, on novel TU |
| Drafting | m2c (not a model at all) | always first |

Route by score and iteration count. Most refinement steps are mechanical and a small
tuned model handles them; the interesting ones are where you spend real money.

---

## What to log NOW so this is possible later

The `attempts` table must capture enough to reconstruct a training example without
re-running anything. If Phase 2 logs less than this, Tier 2 and 3 are unreachable
without redoing the work.

Per attempt, required:

- `func_addr`, `run_id`, `iteration`, and `parent_attempt_id`
- Full `source_code` of the candidate (not a diff — the whole thing)
- The **exact prompt context** that produced it, or a content hash plus enough to
  rebuild it deterministically (KB snapshot version, retrieved examples, m2c output)
- `compiled` (bool) and, on failure, the compiler stderr
- `score`, and the full instruction-level `diff_summary` — not just the number
- `strategy` — which prompt/tactic generated this attempt
- Model identifier and sampling parameters
- Wall time and token cost
- Source/prompt hashes, raw pre-extraction response, extraction status, and stop reason
- The edge action and exact feedback used to derive each child attempt

The explicit parent → child edge is what turns this into refinement data, so **never**
infer a pair from adjacent rows, overwrite an attempt row, or prune failures. Failed
and regressive attempts are the negative examples.

---

## Contamination rules

The first target is a game already decompiled to ~100%, so its source may be in
pretraining data. This invalidates evaluation silently if unmanaged.

1. Prefer **obscure** targets over famous ones. SM64 and Ocarina of Time source is
   certainly in pretraining corpora; a model may reproduce them from memory and your
   held-out numbers become fiction.
2. Never place held-out reference source in a prompt, including as a few-shot example.
3. Sanity check for memorization: prompt the base model for a held-out function with
   *no* asm context. If it produces something close to the real source, that function
   is contaminated — drop it from the eval set and note how many you dropped.
4. Report the contamination-check result alongside every match rate.

---

## Decision points

- Do not start Tier 2 until Phase 2's gate is cleared with Tier 1 only.
- Do not start Tier 3 until the Oracle meets its latency target and Tier 2 has shown
  measurable gain.
- If Tier 1 alone gets you where you need to be, ship that and skip the rest.

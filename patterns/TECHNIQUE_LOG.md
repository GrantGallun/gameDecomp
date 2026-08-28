# Technique log

One row per technique borrowed from the reference decomp and tried here.
**Append, never edit.** A technique that did nothing is as valuable to record as
one that worked — the reference repo's own commits spend most of their words on
what was searched and found inert, and that is the half nobody else publishes.

Rules for entries:

- Measure on **dev** only. Held-out is run once, at the end, and never tuned against.
- Change **one thing** per iteration. Two changes at once cost a clean result
  twice today already.
- Keep the **sample budget fixed** across a comparison. Changing `-n` between
  runs invalidates the exact-count delta.
- Record the paired per-function delta, not just the aggregate — variance on a
  single function spans 82% to EXACT.

| # | Technique | Source | Change | Dev result | Verdict |
|---|---|---|---|---|---|
| 1 | Best-of-N instead of diff-refinement | measured A/B | `sample_one` replaces sequential refine | 8-fn: 3/8 → 6/8 exact | **KEPT** |
| 2 | Anchor feedback on best, not latest | own bug | `refine_one` tracks best attempt | stopped 87→71→23 collapse | KEPT (refine now unused) |
| 3 | Write-source-not-registers prompt rules | failure analysis of `randomNextSecondary` | prompt rules on compound assignment | 7-fn: 6/7 → 5/7, within noise | INCONCLUSIVE |
| 4 | Screen harness-infeasible functions | `build.sh` rejects `do` | `eval/feasibility.py` | 10 of 90 excluded | **KEPT** (eval validity) |
| 5 | Decode array stride from index arithmetic | diagnosis of `unlockRelocatableHeapBlock` | `decode_array_stride` in catalog | that fn 99.17% (stuck) → EXACT first draw | **KEPT** |
| 6 | KB access facts in prompt | thesis, previously unwired | `solver/context.py` | not yet ablated | UNMEASURED |
| 7 | Mirror matched siblings | LEARNINGS "mirror matched siblings verbatim" | `solver/siblings.py` | not yet ablated | UNMEASURED |
| 8 | Triage by score band | failure distribution | `solver/pipeline.py` | 19-fn paired: mean 86.04% → 92.57% | PROMISING, confounded by `-n` change |
| 9 | Narrow-parameter homing | LEARNINGS "IDO codegen: parameter homing and narrowing" | `detect_narrow_params` + hint in `hints_for_asm` | awaiting run | IMPLEMENTED, UNMEASURED |

### Iteration 9 detail

`sw $aN, K($sp)` at entry followed by `andi $rX, $aN, 0xffff` means parameter N
is 16-bit; `0xff` means 8-bit. An `s32` parameter emits neither instruction, so
absence carries information too.

Confirmed before wiring in, positive and negative:

| Function | Declared | Detector |
|---|---|---|
| `setRaceCameraMode` | `(u16, u16)` | `{a0: 2, a1: 2}` correct |
| `getRaceItemEffectType` | `(s32)` | `{}` correct |
| `spawnPatrolCourseObject` | `(s16, s32...)` | `{}` conservative miss |

The third is a deliberate miss, and the learnings file predicts it: IDO only
emits the narrowing reload when the value is *reloaded* rather than used
straight from the home register. A conservative detector is right here — a
wrong parameter type is worse than a missing hint.

This matters because parameter types are structural. Best-of-N can stumble into
a correct statement ordering; it will not stumble into `u16` where it assumed
`s32`, and the resulting mismatch appears as unexplained register-allocation
noise across the whole function.

## Known-inert (do not re-try without new evidence)

- **Diff-guided sequential refinement.** Zero gain across 8 functions; every
  match came on the first attempt.
- **Permuting below 95%.** The permuter moves register allocation only.
  `unlockRelocatableHeapBlock` at 99.167% was a wrong struct size; 300s of
  permuting achieved nothing.
- **Blank-line / statement-line reflow.** The reference repo proved this inert:
  the project builds without `-g`, so inserting blank lines yields a
  byte-identical object.

## Levers the reference repo records as real

Mined from 689 "Improve X match" commits (`patterns/mine_commits.py`):
register allocation, type width and signedness, branch shape (`else` vs
fall-through), local variable presence and naming, frame size, constant form,
inlining and CSE, loop form, statement order, expression spelling, `volatile`.

## External sources (mining queue)

The reference repo is not the only corpus. These are community resources for
exactly our compiler and flags, queued for future iterations. One per
iteration — do not batch.

| Source | Value |
|---|---|
| [OoT `-O2` guide for IDO 5.3](https://github.com/n64decomp/oot/blob/master/docs/guides/-O2%20decompilation%20(for%20IDO%205.3).md) | Canonical, exact same compiler and flags. Densest single source found. |
| [Decompedia: N64 Decompilation Patterns](https://wiki.deco.mp/index.php/N64_Decompilation_Patterns) | Community pattern catalog |
| [Decompedia: IDO](https://decomp.wiki/en/compilers/ido) | IDO quirks reference |
| [decomp-permuter README](https://github.com/simonlindholm/decomp-permuter) | Permuter limits; `--stack-diffs` exists but is documented as weak |
| [Chris Lewis, "Decompiling a N64 Game in 84 Days"](https://blog.chrislewis.au/decompiling-a-nintendo-64-game-in-84-days/) | The SBK author's own account of this exact project |

### Queued from the OoT guide — each needs confirming before it steers anything

1. **Byte-cast shape.** `u32 -> u8` is `(x << 24) >> 24`; `s32 -> s8` uses an
   arithmetic shift. Detectable as `sll 24` + `srl/sra 24`, exactly analogous
   to the confirmed `sll 16 / sra 16` s16 pattern.
2. **`void f(void)` uses 4 more bytes of stack than `void f()`.** A direct
   frame-size lever, and frame size is a hard mismatch.
3. **Comparison normalization.** `x > y` becomes `x >= y + 1` when `y` is a
   constant.
4. **Register allocation order** is `v0, v1, a0-a3, t0-t5` before stack;
   values live across a call get `s*` registers. Useful for reasoning about
   *why* an allocation differs.
5. **Loop unrolling** by 2 or 4 on small loops; suppressed by `continue;` or
   `i++; i--;`.
6. **`&a[i]` in a loop** makes IDO keep an extra loop counter; `a + i` uses
   multiplication instead.
7. **Struct copies reorder** relative to member-by-member copies — relates to
   the confirmed `bulk-struct-copy` pattern.
8. **Short switches hoist the default case** to the top so other cases fall
   through.
9. **Rodata literals are "really const"** and their loads may be hoisted to the
   top of a function into saved registers; `extern const` is not.
10. **Branch-likely** is emitted when IDO cannot reorder an instruction into
    the delay slot.

Note: `n64-decomp-workbench` is listed in the repo's requirements and has a
wrapper at `tools/decomp-workbench-compile`, but the module does not import
under that name in this venv. It is documented as distinguishing structural
from register-allocation mismatches, which is exactly the triage problem —
worth resolving in a later iteration.

## Failure modes and their routes

| Signal | Meaning | Tool |
|---|---|---|
| 100%, verified exact | done | commit |
| 0 diffs, NOT exact | **relocation mismatch** — wrong symbol reference | fix symbol spelling, not code |
| ≥95% | register allocation | decomp-permuter |
| 80–95% | wrong struct size / field types | stride decode + KB facts + sibling |
| <80% | wrong shape | mirror a matched sibling |

## Long-context literature (searched 2026-08-28)

Both of the ideas we just tested -- split the reading into shifts, attach a
lexicon so the model looks a fact up instead of rescanning -- appear in
published work, arrived at independently. That is corroboration, and it also
hands us the parts we had not built.

### The refusals are almost certainly abstention, not safety

Chroma's *Context Rot* report (18 frontier models, incl. GPT-4.1, Claude 4,
Gemini 2.5, Qwen3) finds degradation at **every** input-length increment
tested, and reports that some models **abstain when uncertain** while others
hallucinate confidently. Our gpt-oss refuses ~50% on large/huge and 0% on
tiny/small; region reads at ~45 lines refuse 0 of 18. That is the abstention
profile, not a safety profile -- which is what killed the copyright-framing
hypothesis empirically and now has a mechanism behind it.

Two further findings bear directly on assembly:

- **Distractor interference.** Semantically similar but irrelevant content
  degrades performance *beyond* what length alone explains, and the effect
  amplifies with length. 300 near-identical MIPS instructions are close to a
  worst case for this: every line looks like every other line.
- **Coherent haystacks score worse than shuffled ones**, across all 18 models.
  Structure creates plausible-looking distractors.

This is the first mechanistic account we have for why the wall is where it is,
and it predicts assembly should be unusually bad for long context.

### WaDec (ICSE'25, WebAssembly) -- the recombination we lacked

Reports 52.11% recompilability. Its slicing algorithm has four parts, and we
currently implement one:

1. **Slice at loop boundaries** -- each snippet holds at most one loop plus
   arbitrary conditionals. We cut at any branch or label, which is cruder.
2. **Nested loops are replaced by markers** and decompiled separately; the
   markers are the reassembly points. Hierarchical, and it means no slice ever
   holds a nested body.
3. **Temporal context** -- each slice is told the variables already defined by
   previously decompiled slices. This is sequential composition: slices emit
   CODE, not prose, and later slices build on earlier ones. Our composer
   instead reads prose summaries and writes the whole function in one shot.
4. **Spatial context** -- declarations of called functions, because argument and
   return counts affect stack balance. Our lexicon lists call sites but not
   signatures.

Plus an Offset2string map from offsets to string constants -- a lexicon, for
the same reason we built one.

Caveat, stated because it matters: the paper has **no ablation isolating the
slicing**, so its contribution to that 52% is not separately measured. Take the
design, not the attribution.

### AutoDecompiler -- multi-turn beats single-turn

Uses stage-aware feedback from compiler errors, execution failures and failed
tests, and consistently beats single-turn at equal model size. Our feedback
signal is strictly stronger (byte-exact object diff, not re-execution). The
obstacle is ours alone: measured prefix-exact depth is 0, so there is no
verified prefix for a turn to preserve.

### Prompt compression -- the wrong kind is available off the shelf

LLMLingua reports up to 20x compression with minor loss, but it is **token-level
and lossy by design**: it drops tokens a small model judges redundant. For
byte-exact matching that is disqualifying -- a dropped immediate is a wrong
constant. Our strip_asm is the other kind: lossless *structural* removal of
fields (file offset, vram address, encoding) that provably cannot constrain the
C source. Keep that distinction. Do not reach for LLMLingua here.

### What this implies, in priority order

1. **Sequential composition** (WaDec 3) -- slices emit C and carry forward the
   declarations. This is the biggest gap between our design and theirs.
2. **Loop-aware slicing with markers** (WaDec 1-2), replacing branch-boundary
   cuts.
3. **Callee signatures in the lexicon** (WaDec 4) -- cheap, mechanical.
4. **Position** -- lost-in-the-middle predicts the lexicon should sit adjacent
   to the instruction at the END of the prompt, not buried ahead of the
   assembly where it is now. Nearly free to test.

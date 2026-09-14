# Decompedia review and compiler checks — 2026-09-05

Latest: [seven queued claims and graphics follow-up](FOLLOWUP.md), including
the CPU-only packet audit and target-specific checks prompted by the subsequent
assessment. The counts below describe the initial pass only.

The review found useful unused controls and corrected overconfident guidance.
It produced **zero incremental exact matches**. Ten game-candidate attempts
were logged (29694–29703), plus ten standalone compiler-probe builds. No model
calls, heldout evaluations, target function-body reads, promotions, campaign
checkpoint changes, or game-source replacements were performed.

## Sources reviewed and local relevance

The starting point was the supplied N64 page, also available in
[Decompedia's source repository](https://github.com/decompals/decompedia/blob/main/platforms/nintendo-64.md).
This was a compiler/matching review, not an exhaustive audit of graphics,
microcode, every listed project, or every hardware reference.

| Source | Finding and disposition |
|---|---|
| [Decompedia IDO](https://decomp.wiki/compilers/ido) | Compiler ecosystem overview. Its recomp/decomp distinction matters; the existing local build already runs recompiled IDO. |
| [IDO static recomp](https://github.com/decompals/ido-static-recomp) | Supports 5.3 and 7.1. Retain the installed compiler and fingerprint its binaries; no upgrade was needed for these tests. |
| [IDO matching decomp](https://github.com/decompals/ido-matching-decomp) | Compiler-internals research source. Its README distinguishes per-function matching from whole executable matching. It is not evidence that a replacement 5.3 optimizer is ready. |
| [Historical IDO debugger](https://github.com/n64decomp/ido) | Interactive uopt inspection; README warns of 32-bit platform constraints. Separate project from the matching decomp above. |
| [OoT IDO 5.3 -O2 guide](https://github.com/n64decomp/oot/blob/master/docs/guides/-O2%20decompilation%20(for%20IDO%205.3).md) | Mixed observations and explicitly tentative explanations. Tested the stack-related claims below. It also documents optimizer/code-generator debug traces; these remain a possible diagnostic path, not a newly implemented feature. |
| [m2c documentation](https://github.com/matt-kempster/m2c) | Exposes `--no-andor`, `--no-switches`, localized `# GOTO`, stack-structure context and limited cross-function inference. Local adapter previously exposed none of these controls. Tested only `--no-andor`; the others remain hypotheses. Existing header/rodata context is already wired. |
| [Permuter README](https://github.com/simonlindholm/decomp-permuter) and [default passes](https://github.com/simonlindholm/decomp-permuter/blob/master/default_weights.toml) | Mutates types, conditions and source expressions, not just register allocation. Manual macros can constrain alternatives. Stack offsets are ignored by default; stack-aware scoring is described as weak. Corrected our categorical capability claims without changing routing thresholds. |
| [asm-differ](https://github.com/simonlindholm/asm-differ), [splat](https://github.com/ethteck/splat) | Existing assembly comparison and splitting tools already fit their respective roles. No replacement or installation justified by this review. |
| [Decompedia decomp.dev guide](https://decomp.wiki/tools/decomp-dev) | Highlights the difference between matching builds and progress reporting when inline assembly is present. Relevant to preserving our solved/assisted/recovered distinctions; no reporting integration added. |

Access limits: the old `wiki.deco.mp/.../N64_Decompilation_Patterns` URL could
not be retrieved. The wiki m2c endpoint failed to fetch, so its upstream README
and installed CLI help were used. The projects page yielded no usable body.
Those resources are **unresolved**, not reviewed or ruled out. The OoT guide is
an existing queued companion source, not claimed to have been discovered anew.
Compiler tracing also appears in already-mined commit messages in
`eval/results/provenance.json`; that establishes prior exposure, not active wiring.

## Experiment 1: fixed stack-cost claims

Reproduction, inside WSL from this checkout (use a new output directory):

```bash
/home/grant/decomp/sbk1/.venv/bin/python eval/experiments/decompedia/run_review.py \
  --output eval/experiments/decompedia/results-v2
```

[Receipt](results-v1/receipt.json) records compiler hashes, the resolved game
recipe, source hashes and assembly/object artifacts. Synthetic probes use direct
IDO with the resolved C flags; game candidates use the existing project oracle,
object certificate and frontend. The tested recipe is `-O2 -mips1 -G 0` with the
remaining defines/includes preserved in the receipt. No debug flags changed.

| Single change | Frame bytes, baseline → variant | Machine-code result |
|---|---:|---|
| Leaf definition: empty → void parameters | 0 → 0 | Identical `.text` |
| Calling definition: empty → void parameters | 24 → 24 | Identical `.text` |
| Callee declaration: empty → void parameters | 24 → 24 | Identical `.text` |
| Ignored callee return: void → int | 24 → 24 | Identical `.text` |
| Name a one-use expression temporary | 24 → 24 | Identical `.text` |

Four development parents were frozen before compilation. Three had parameters
and were explicitly declined: `waitRaceIntroFlyoverShortPanFinal`,
`initCharacterSelectCourseStatsBadge`, `initCharacterSelectCoursePreviewPanel6`.
They were not replaced with favorable cases. The eligible control,
`fadeInEndingCreditsFlow`, stayed object-exact (29694/29695), but its empty-list
variant failed `-Werror=strict-prototypes`. It is not an accepted repair.

Conclusion: reject universal fixed-cost interpretations, not every possible
prototype or temporary-variable effect. These are counterexamples, not a claim
that the compiler never responds to such changes. `frame_hint` now states the
observed frame and asks for compiler verification instead of prescribing the
unconditional four-byte or named-temporary rules. A provenance-bearing review
entry was added to the pattern catalog; it has no automatic detector.

## Experiment 2: m2c compound-condition reconstruction

```bash
/home/grant/decomp/sbk1/.venv/bin/python eval/experiments/decompedia/run_m2c.py \
  --output eval/experiments/decompedia/m2c-no-andor-v2
```

[Receipt](m2c-no-andor-v1/receipt.json). Selection was all four `game_medium`
functions in `autonomy_wavefront_dev_24_v1.json`, checked against the existing
heldout-name guard. Each arm received the same normalized assembly and identical
preprocessed headers, one draft and one oracle invocation; no source repair or
model generation was mixed into either arm. Parent source bodies were not used
to create these drafts. This is deliberately inspected, header-assisted DEV.

| Function | Default → no-andor | Interpretation |
|---|---|---|
| updateRaceTypeSelectCursor | 88.821 → 88.821 | Identical source; both frontend-pass |
| drawRaceTypeSelectCornerSprites | exact → exact | Identical source; existing success, no incremental match |
| drawRaceSetupSaveChoicePrompts | guard rejection → guard rejection | Source changed, but both contain do-while; codegen effect untested |
| drawMultiplayerRaceHud | compiler failure → compiler failure | Identical source; unresolved pseudo-C declarations |

No general benefit or harm is established: the only changed draft could not
reach compilation. The option is available through `m2c_input.draft` for explicit
experiments and is recorded in metadata. It is **not enabled by the campaign**.
The regression test checks opt-in forwarding, identical normalized input and
preservation of the oracle assembly. Focused validation: 80 passed across
`test_m2c_input.py`, `test_m2c_context.py`, `test_units.py` and
`test_completion_campaign.py`; tracked-file whitespace checks passed.

## Next distinct tests

1. Replay the changed save-choice pair through the existing common source
   normalization, identically in both arms, then score it. This resolves the
   guard obstruction before judging no-andor's effect.
2. Use `--no-switches` only on a DEV target with an irregular-switch residual;
   preserve the default draft as a control. No benefit inferred from this review.
3. Test `_m2c_stack_<function>` context on a frame-layout residual with cited
   stack accesses. Treat aggregate types as hypotheses and obey exact frame size.
4. For an isolated register residual, inspect the existing compiler-trace
   tooling before building another adapter. Require stock-versus-traced byte
   equality before using debug traces as explanatory evidence.

These are separate tests, not a bundled pipeline change. Historical failures of
unbounded permutation and prompt enrichment remain valid within their measured
scope; this review does not turn them into successes.

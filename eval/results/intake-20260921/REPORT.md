# Does the intake lever generalise? Measured, then the defects it exposed, fixed and re-measured

Executed 2026-09-21 from `NEXT.md`. Steps 1 and 2 were run as specified; their results contradicted the
plan's premises, the contradictions traced to six mechanical defects, and all six are now fixed with
tests and a re-measurement. Receipts for every number: `FIXES.json`.

| # | question | answer |
|---|---|---|
| 1 | Does the intake lever generalise past small functions? | **No.** The four intake actions convert 0 of 40 across size tiers. |
| 2 | Is the action dataset composed of states where no action helps? | **No.** The original actions improve the certificate on 7 of 40 and convert 2 — the opposite of the expected control. |
| 3 | Then what is the blocker? | **The `?` token in parameter lists, which `m2c_placeholders` could not rewrite** — one regex case, worth 5% → 22.5% conversion and 0 → 2 certified matches. |
| 4 | After fixing that and five other defects | Intake arm **9 of 40 converted, 2 exact**. Control arm **13 of 40 improved, 9 compiling, 2 exact** — up from 7/2/0. |

---

## 1. The frame

`--size-buckets` draws from the whole unsolved population instead of the head of `order by size asc`,
from the largest measured failure class in the KB (373 functions whose latest attempt carries
`cfe: Syntax Error`), apportioned across the project's existing tiers (`eval/distance.py`), `--per-tier 8`:

```
pool  by tier:  tiny 5   small 37   medium 130   large 139   huge 416
frame by tier:  tiny 2   small 10   medium 10    large  9    huge  9      (40, shortfall 0)
```

The old frame (`frozen.json`) is a different population — median ~50 bytes against ~1,400 here. That
difference is the whole reason Step 1 asked the question.

**The pool moves.** Two builds of the identical command returned two different 40s
(`drawControllerPakRaceRecordSaveStatusMessage`, `renderRacePickupIdle` out;
`initMainMenuSceneModelParts`, `initMultiplayerCourseSelectMenu` in), because the failure class is
re-derived from the latest attempt while a live campaign (`eval.fast_campaign`, 3 workers) writes attempts
into the same KB. `--frozen` pins membership for the arms; a freshly built frame is only reproducible
against a quiet KB. All rates below come from one stored frame, and every arm's identity is checked
against it (same function set, same draft hashes, 0 differing).

## 2. Step 1 — the intake sequence: 0 of 40

Identical in the build and the frozen replay, `harness_clean: true`:

| action | fired | compiled | exact | declined | crashed |
|---|---|---|---|---|---|
| `header_variant` | 31 | 0 | 0 | 9 | 0 |
| `rewrite_do_while` | 17 | 0 | 0 | 23 | 0 |
| `globals_variant` | 1 | 0 | 0 | 38 | **1** |
| `opaque_variant` | 0 | 0 | 0 | 40 | 0 |

`header_variant` fired on 31 of 40 and converted none, and its whole contribution was an `#include` line:

```
#  updateRaceResultsFlow
   #include "common.h"                     #include "common.h"
                                          +#include "game/race/player/race_player_input.h"
                                          +#include "game/audio/sound_manager.h"
```

On the old small frame it was the only mechanism that converted anything (3 of 12, 1 exact). It fires, it
changes the source, and an include cannot reach a `Syntax Error` on the function's own signature line —
which is where these drafts die.

## 3. Step 2 — the negative control, which refuted its own premise

The original seven wired actions (`eval.tool_registry.ACTIONS` minus the four intake actions minus `stop`),
one at a time from the same frozen drafts, judged by the certificate. **After the fixes:**

| measure | before fixes | after fixes |
|---|---|---|
| states where some original action improved the certificate | 7 of 40 | **13 of 40** |
| states converted to compiling | 2 | **9** |
| certified matches | 0 | **2** |
| states where the source moved | 40 | 29 |

| action | fired before | fired after | n/a before | n/a after |
|---|---|---|---|---|
| `redraft` | 40 | **0** | 0 | 0 |
| `resolve-placeholders` | 7 | **15** | 0 | 0 |
| `invert-mutations` | 11 | 11 | 0 | 0 |
| `regalloc-search` | 0 | 0 | 6 | 6 |
| `compile` | 0 | 0 | 0 | 0 |
| `diffrepair` | 0 | 0 | 40 | 40 |
| `uopt-trace` | 0 | 0 | 40 | 40 |

**The premise `NEXT.md` expected this run to confirm — "the action dataset was composed of states where
no action helps" — is false.** One fifth of the largest failure class improves under an action that was
already available, and nine states compile that did not. The zero certified matches in every arm of the
adapter evaluation cannot be explained by unreachable states.

## 4. The six defects, and what each was worth

### 4.1 `m2c_placeholders` could not type a named parameter *(the big one)*

```c
u64  __ll_mul(s64 a0_unk0, ? a0_unk4, s64 a1_unk0, ? a1_unk4) {   /* placeholders() -> [] */
void osSyncPrintf(s8 *fmt, ? arg1, ? arg2, ? arg3, ...) {          /* placeholders() -> [] */
```

`DECL_LINE` was anchored `^[ \t]*` and `PARAM`'s lookahead required punctuation after the token, so
`? name` in a parameter list matched **neither** rule. Over the frame: 36 raw `?` tokens, 18 found, 2
drafts rewritten, **12 tokens left in a declaration position**. The module is documented as covering
"the placeholder also appears inside parameter lists".

The fix extends `DECL_LINE` with a declaration boundary that admits a line start, `(`, or `,` — and
`PARAM` stays, because it owns the *nameless* shape `void g(? *, s32);`. The two rules are a union, not a
hierarchy: dropping either loses a shape the other cannot see. A test asserts the ternary
(`return a ? b : 0;`) is left byte-identical, because the first attempt at this pattern mangled it.

| arm | converted | exact |
|---|---|---|
| module before | 2 / 40 (5.0%) | 0 |
| module after | **9 / 40 (22.5%)** | **2** |

Two passes in one process, opposite orders, **0 flaky states**, and the union of module + extension now
equals the module (0 cases either way) — which is the evidence the extension was absorbed rather than
merely measured.

### 4.2 The probe's sequence was not the campaign's order

`eval/intake_probe.SEQUENCE` omitted the placeholder rewrite entirely and ran the do-while lowering
unconditionally. `eval/completion_campaign._intake` applies the rewrite to every draft **first**, and
lowers a do-while only when the compiler reported one. Fixed to mirror it, with the do-while gate:
17 phantom fires → 0 fired / 40 gated.

### 4.3 `redraft` "fired" on 40 of 40 by deleting a wrapper

It re-ran the m2c *binary* while the candidate came from `workspace.m2c_draft`, which sanitizes the
assembly and prepends `#include "common.h"`. Two different producers, so the comparison was between a bare
draft and a wrapped one: 617 characters → 416 on `copyGfxCommandBlockToScratch`, whose error count then
went 1 → 6. Fixed to go through the canonical producer; `needs` now declares `repo` and `function`.

The old test patched `subprocess.run`, so it *could not* see a producer mismatch. It now drives the real
producer and asserts both the no-change and the failed cases.

### 4.4 `uopt-trace` was declared wired and could not run

Its runner guessed five entry-point names on `solver/uopt_trace`, which is a **parser** — it reads a
`uoptlist` dump. None of the five exist, so the action declined 40 of 40 while passing
`unwired_actions()`. The real producer is `solver/uopt_diagnosis.traced_compile` (three compiles through
the patched `-zdbug:5/6` toolchain) and the consumer is `diagnose`. Both already existed. The runner now
calls them, declines by name when the traced compiler is not built or another traced compile holds the
repo's `uoptlist`, and the registry declares all six real inputs.

### 4.5 `build_context` never populated three declared fields

`Context` has declared `target_dump`, `workspace` (and `dump`) since it was written, and `build_context`
set none of them: `regalloc-search` declined 6 of 40 on "the context does not carry target_dump". All
three are now populated from the oracle's own artifacts, with the candidate dump read at the moment of
the compile that produced it — a dump read later describes whichever candidate was compiled last.

### 4.6 `diffrepair` is unreachable in this phase, and now says so

It needs a diff, and a candidate that does not compile has none — 40 of 40 declines for a reason no
plumbing removes. It now reports that in those words and names the phase where it becomes reachable,
instead of "the context does not carry diff".

## 5. `rmonPrintf`: score 100.0, not exact — and it is **not** a failure

The one unresolved anomaly from the first pass. It is none of the three explanations that were on the
table, and the certificate is not the thing that is wrong:

```
attempt.exact            False
certificate status       object_sections_differ
normalized dumps         identical (114 vs 114), diff empty
boundary.function_exact  TRUE — status `function_exact_pending_integration`, no error
integration_ready        True
```

`rmonPrintf` and `osSyncPrintf` are the **same code** — both empty variadic stubs, normalized dumps
byte-identical, first 16 text bytes identical — and they get opposite verdicts for a mechanical reason:

* `osSyncPrintf`: `.text` is 32 bytes for a 32-byte function → `object_sections_exact`, `Attempt.exact` True
* `rmonPrintf`: `.text` is 32 bytes for a 28-byte function with 4 bytes of assembler alignment inside the
  same TU → `object_sections_differ`, and the function-extent certificate that **does** verify it writes
  `verification["function_boundary"]`, which `Attempt.exact` never reads

So the ROM-backed extent certificate works and the promotion path looks elsewhere:
`eval/completion_campaign.py:210` reads the boundary and sets `function_exact_pending_integration`;
`solver/workspace.py` sets `exact = verification["exact"]`, which the boundary path does not touch. Every
harness keying on `attempt.exact` — this session's probes included — scores a certified-correct function
as a failure. **Not patched**: `Attempt.exact` drives the ratchet, and the boundary certificate says
`requires_isolated_integration: True` and excludes the whole ROM on purpose. Recorded with a receipt
(`_boundary_verdict.py`, `_text_tails.py`).

## 5. Where the remaining work is (added after the fixes)

The reach on the frozen 40: **2 certified, 7 compiling-not-exact, 31 still not compiling.** Grouped by the
construct in the draft's own text rather than by the compiler's message (`Syntax Error` is a symptom):

| n | construct | what exists behind it |
|---|---|---|
| 11 | unclassified | cfe names a plausible statement; the caret is on its identifier; not diagnosed |
| 8 | `->unk-4`, `->unk-604` — negative field offsets, not valid C | **nothing**: no module rewrites `unk-N` |
| 4 | redeclaration against an injected header | **self-inflicted by `header_variant`**; nothing dedupes the two |
| 2 | `(bitwise f32)` | `solver/m2c_context.py:130` lowers it — not in the intake sequence |
| 2 | `(unaligned s32)` | `solver/m2c_byte_view.py:334` handles it — reached from `compile_recovery`, not from intake |
| 2 | `?` inside a struct body | `m2c_placeholders` now finds these; typing them is what remains |
| 2 | `->name` on an untyped pointer | `opaque-struct`'s class; declined 40 of 40 without reaching one |

Eight of those are a construct with a module already written for it that the intake route does not call,
or a defect the intake route injects itself.

**Two hypotheses raised in this session and disproved — recorded so they are not re-run:**

1. *`--valid-syntax` produces the syntax-clean draft, so the 31 are a flag.* Measured: that arm converts
   **1 of 40 (2.5%)** against 9 of 40 (22.5%) without it. It re-encodes field accesses as
   `M2C_FIELD(...)`, a different non-C form, and drops the recovered parameter list. Worse, not better.
2. *`Syntax Error` on a declaration line means an undeclared type name.* Measured: for all 8 states where
   a project header declares the name, adding that header moved the first error by **exactly one line** —
   the include itself — and removed nothing. The type name is downstream of the real blocker. This is the
   same mistake as reading `Syntax Error` as a cause: 24 of 31 states carry an undeclared type name, and
   22 of those names are not declared in any header at all.

**Some of the "unclassified" lines are not syntax at all.** `updateMultiplayerCourseSelectMenu` fails at
line 152 on `var_s1_2->unk-1828 = 3;` — the same negative-field-offset class, reached at a different
point. The classification is a first-error split, not a partition of causes.

## 6. Two more defects, shipped and verified

### 6.1 `header_variant` was making 4 of 40 states unbuildable

It added a header for a NAME found in the draft — including the name the draft itself **defines**:

```
cfe: redeclaration of '__allocParam'; previous declaration at line 256 in 'include/synthInternals.h'
    the header provides : ALParam *__allocParam(void);
    the draft says      : s32 *__allocParam(void) {
```

Same for `__freeParam`, `alMainBusPull`, and `typedef struct RacePlayer RacePlayer;`. A definition is not
a declaration to be reconciled — it is the thing being compiled.

**Fixed and verified:** `header_variant` fires **31 → 24**, all four states no longer report a
redeclaration, **0 regressions**.

### 6.2 `base->unk-N` is not C, and nothing owned it

The largest identified construct among the 31 blocked states (8 of 31 by first error). `->unk-4` is not a
member access: the identifier ends at `unk` and `- 4` is a subtraction. New module
`solver/m2c_negative_offset.py` lowers it to `(*( s32 *)((unsigned char *)base - 0x4))` — the spelling is
forced, because C needs a pointee type to fix the scale — registered as the `negative-offsets` action and
placed in the intake sequence. 9 tests.

**Correction to an earlier claim in this report.** I said no module rewrites `unk-N` and that the sibling
`solver/negative_field_repair.py` looked dead. It is neither broken nor unwired: it gates on clang's
`member reference base type` diagnostic and is called from `solver/modelrepair.py:759`, and it declines
here only because **the intake route never runs the clang frontend**, so the gate has nothing to match on.
Running that chain by hand (`_frontend_chain.py`) shows clang does emit the diagnostic — 4 to 19 per
blocked state — and that it reports **every** blocker at once where cfe truncates at the first.

### 6.3 What the re-measurement says

| arm | converted | exact |
|---|---|---|
| before any fix | 0 / 40 | 0 |
| after the six wiring fixes | 9 / 40 | 2 |
| after these two | **9 / 40** | **2** |

Frame identity identical (same set, 0 differing drafts), no regressions. Two defects fixed and verified
without moving the conversion count. That is not a null: four states are buildable again and
`header_variant` stopped making work for itself. It says the rate is now set by states these two do not
reach — and that the next step is a bigger frame, not another targeted repair, because a 40-state frame
whose membership moves 4 between runs cannot resolve a two-state effect.

## 7. Tooling or training?

**Tooling — 8 defects for 8, every one mechanical**: a fallback, a default, a comparison, a missing field,
or an edit the route inflicted on itself. Not one was a missing model capability. And the five measurements
stand: base 0.600 / adapter 0.833 acceptable against 0 certified in every arm; all 13 control-arm
improvements attributable to one action, so a trained policy has one useful lever and nothing to choose
between; training on states where no action helps teaches nothing; and the instrument cannot see its own
effect at this frame size.

## 8. What did not work

`--placeholder-widths`: resolving placeholders with types derived from the target
(`eval/binary_types.types_for`) changed **nothing** — 9 of 40 either way, every verdict identical.
The cause is mechanical, not a null: `binary_types.NAME` requires m2c's `arg0` / `var_a1` / `sp18`
spellings, and these drafts name parameters `a0_unk4` / `a1_unk4`, so the target typed **2 of 15** drafts
and the rest kept the `s32` default. `eval/binary_types.py` documents this same gap one level up ("every
caller passes one argument"). Extending that name grammar is a real, unclaimed piece of work.

## 7. What is not claimed

* 9 of 40 and 13 of 40 are rates on one failure class of one target. The frame moved by 2 of 40 between
  builds while a campaign was writing the KB, so a fresh build is not a controlled sample.
* 22.5% is a **compiles** rate. Two of forty are certified. No match was promoted and the ratchet did not
  move: `git status` on the target repo is unchanged apart from the workspace artifacts the oracle writes,
  and global match count was not touched.
* 31 of 40 drafts carry no `?` and still do not compile. Once the front door opens their first error is
  `Selector requires struct/union pointer` — `opaque-struct`'s class, which declined 40 of 40 here and
  remains untested rather than disproven.
* No model was called anywhere in this work.

## 8. Reproduce

```bash
cd /mnt/c/Code/gameDecomp && export PYTHONPATH=$PWD
PY=/home/grant/decomp/train-venv/bin/python

bash .cache/recon/class_frame.sh            # Step 1: frame + whole-frame frozen replay
bash .cache/recon/class_control.sh          # Step 2: the original seven actions (WANT=40 TAG=class-control)
bash .cache/recon/post_fix_measure.sh       # both arms after the fixes
bash .cache/recon/verify_fixes.sh           # imports + the 97 tests

$PY eval/results/intake-20260921/_before_after.py        # frame identity, then the rates
$PY eval/results/intake-20260921/_post_fix_table.py      # every arm, before and after
$PY eval/results/intake-20260921/_placeholder_clean.py   # the two-rule comparison, twice
$PY eval/results/intake-20260921/_score100_case.py       # the rmonPrintf classification
```

## 9. Files

| file | role |
|---|---|
| `solver/m2c_placeholders.py` | the declaration-boundary fix; `PARAM` retained for the nameless shape |
| `eval/intake_runners.py` | new `resolve_placeholders` runner (the campaign's first intake step) |
| `eval/intake_probe.py` | campaign order, do-while gate, `--size-buckets`, per-tier rates, whole-frame frozen replay, action-crash vs harness-error split |
| `eval/intake_control.py` | the negative control; applicability reported separately from improvement |
| `eval/tool_runners.py` | `redraft` through the canonical producer; `uopt-trace` wired to the real producer; `diffrepair`'s phase boundary stated |
| `eval/tool_agent_run.py` | `target_dump`, `dump`, `workspace` populated; candidate dump read at compile time |
| `eval/tool_agent.py`, `eval/tool_registry.py` | `Context.workspace`; `needs` corrected for three actions |
| `tests/test_m2c_placeholders.py` | 5 new tests: the named-parameter shapes, the union, the declines |
| `tests/test_tool_boundary.py` | `redraft` rewritten against the real producer + 2 decline tests |
| `tests/test_intake_frame.py` | 10 tests on frame construction |
| `FIXES.json` | every defect, its residual, its test, its before/after numbers |
| `post-fix-intake.json`, `post-fix-control.json` | the re-measurement |

## 10. Next

1. **Extend `binary_types.NAME`** to the `a0_unk4` / `t1_unk8` / `sp1C_unk` spellings m2c actually emits
   for parameters. It typed 2 of 15 drafts; the arm that would use it is already built and measured
   (`--placeholder-widths`), so the change is testable in one command.
2. **`opaque-struct` is now the largest untested row.** With the front door open, the next error on nearly
   every remaining draft is `Selector requires struct/union pointer`, and `opaque_variant` declined 40 of
   40 on this frame — because the drafts never reached that error before, not because it cannot fire.
3. **Step 3, train only where a positive exists** — now with a real dataset: 9 states where
   `resolve-placeholders` opens the door, 2 certified, plus 13 states in the original action space. The
   parent action is already in the eight-action space the adapter was trained on, so the "the policy has
   never seen these actions" caveat does not apply.
4. **Decide the `rmonPrintf` class deliberately.** Functions whose bytes certify under
   `function_boundary` but whose TU carries alignment padding are invisible to `attempt.exact`. Whatever
   the answer, it should be a decision about promotion, not a side effect of where a receipt is stored.

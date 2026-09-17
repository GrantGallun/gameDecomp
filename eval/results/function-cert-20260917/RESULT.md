# Matches the pipeline produced and never counted

2026-09-17. Found while chasing the goal "convert blind statement-permutation search into computed
permutation": the order-permutation population turned out to be empty, and the census that established
that turned up something much more valuable — **a population of functions whose bytes match the ROM and
which the match count does not include.**

## 1. The permutation premise was already exhausted

Across the 133 live compiling non-exact functions, by residual cause
(`eval/order_class_census.py`, `eval/results/order-class-20260917.json`):

| cause | n |
|---|---:|
| `not-a-permutation` — the hunk changes the instruction multiset | **126** |
| `colouring` | 5 |
| `order` | **1** |
| `no-hunks` | 1 |

The single `order` residual is `updateRacePlayerMode16AerialTrick`, whose moved lines are **loads** —
and `patterns/catalog.py` already records that applying the analogous change **regressed** it
(99.712 → 97.115, regalloc 0 → 36). There are **zero** store-order residuals left; Fstop was the only
one and it is closed.

So the 7,305-attempt / 0-exact blind permutation spend is not "a computable answer waiting to be
computed". The diff-read lever has no queue behind it. **That is a refutation of the goal's premise,
and it is worth more than another search would have been.**

## 2. The `no-hunks` residual was a measurement hole

One function had a compiling, non-exact attempt with an **empty diff and score 100.0**:
`osSpTaskStartGo`, strategy `campaign-intake:do-while-for-break`, 20 attempts, 0 exact.

`solver.workspace.score` only consulted the ROM-backed byte certificate when
`normalized_exact or operand_only_diff(diff_text)`. `operand_only_diff` inspects the diff's **changed
lines**, so an EMPTY diff returns False — the strongest possible case for certification was the one
case that was skipped. Fixed: a hunkless diff now certifies too. That is safe by construction, because
`byte_certificate.certify` **is** an object/ROM comparison and can only find a real match, never
fabricate one.

**The fix did not promote `osSpTaskStartGo` — it revealed why it is not promoted**, and that is the real
finding.

## 3. Two uncounted classes, measured

`eval/function_certificate_census.py` over every certificate in the build tree (1,842 certificates,
810 functions):

| | |
|---|---|
| `object_sections_exact` | 1,670 |
| `object_sections_differ` | **172** |
| — of those, `normalized_assembly_exact` is TRUE | **122 certificates, 40 functions** |
| — of those, **`function_boundary.function_exact` is TRUE** | **24 certificates, 11 functions** |

The 11 with a ROM-backed **function** certificate:

| function | `.text` target → candidate | normalized asm |
|---|---|---|
| `calculateFixedAngleBetweenXZPoints` | 64 → 48 | True |
| `drawMainMenuModeSelectMenuOptions` | 816 → 816 | True |
| `fadeInRaceGameplayViewports` | 1680 → 1680 | False |
| `func_8005905C` | 1216 → 1216 | True |
| `initControllerPakFileDeleteFlow` | 544 → 544 | False |
| `initMainMenu` | 944 → 944 | False |
| `initRaceTypeSelectMenu` | 480 → 480 | False |
| `osSpTaskStartGo` | 80 → 64 | True |
| `rmonPrintf` | 48 → 32 | True |
| `updateRaceCameraMenuPreview` | 48 → 32 | True |
| `updateRacePlayerPostUpdateAttack` | 112 → 96 | True |

Two shapes, and they are different defects:

* **Equal `.text` size, sections still differ** (`drawMainMenuModeSelectMenuOptions`,
  `func_8005905C`, `initControllerMainMenu`, `initControllerPakFileDeleteFlow`,
  `initRaceTypeSelectMenu`): same extent, so the difference is relocation/composition, and
  `relocation_order_equivalent` is False on every one of them.
* **Candidate `.text` is SMALLER** (`osSpTaskStartGo` 80→64, `rmonPrintf` 48→32,
  `updateRaceCameraMenuPreview` 48→32, `updateRacePlayerPostUpdateAttack` 112→96,
  `calculateFixedAngleBetweenXZPoints` 64→48): the target object carries **more than this function** —
  another symbol or padding — while the function's own bytes match the ROM.

`function_boundary.certify`'s own scope line is the honest limitation: *"annotated function bytes and
external call relocations only"*, with `whole_rom_verified: false`. So this is **not** the same claim as
"the object section is byte-identical", and it is not the whole-ROM tier either.

## 4. Why I did not promote them

Counting these as matches would move the headline, and this project's rule for that situation is
already written down twice in `eval/status.py`: a tier change that LOWERES or RAISES the reported
number is the **operator's call**, documented rather than applied silently (see the two KNOWN GAPs).
Promoting 11 functions on a certificate whose scope excludes final link layout would be exactly the
"hypothesis changing behaviour" failure this project keeps catching.

What is now available instead is a **receipted, per-function list** with the evidence attached, and a
tool to re-derive it: `eval/function_certificate_census.py`.

**The decision to request:** whether `function_boundary.function_exact: true` is a match tier in its own
right — a ROM-backed per-function byte comparison, distinct from both the object-section tier and the
whole-ROM tier. If yes, it is 11 functions today and the tool re-measures the population on demand.

## 5. The audit: is the extra `.text` padding, or code?

`ws/target.o` is not a single-function object. Measured directly:

| function | KB function size | target `.text` | delta | normalized dump |
|---|---:|---:|---:|---:|
| `osSpTaskStartGo` | 64 B (16 insn) | **80 B** (0x50) | +16 | 15 lines |
| `rmonPrintf` | 28 B (7 insn) | **48 B** (0x30) | **+20** | 7 lines |
| `updateRaceCameraMenuPreview` | 32 B (8 insn) | **48 B** (0x30) | +16 | 7 lines |
| `drawMainMenuModeSelectMenuOptions` | 816 B (204 insn) | **816 B** (0x330) | 0 | 203 lines |

**The deltas are not always alignment.** `+16` on `osSpTaskStartGo` is consistent with four trailing
nops (its normalized dump has 3 in-body nops and `strip_padding` removes the `jr ra` delay slot, so
15 dump lines + the stripped delay slot = the 16-instruction function). But `rmonPrintf` is `+20` —
**five instructions, not a 16-byte multiple** — so `target.o`'s `.text` holds material that is not
padding and is not this function.

So the two shapes have different explanations, and only one of them is the documented
"compiler padding rather than code" unreachability:

* **size mismatch** (`osSpTaskStartGo`, `rmonPrintf`, `updateRaceCameraMenuPreview`,
  `updateRacePlayerPostUpdateAttack`, `calculateFixedAngleBetweenXZPoints`): the section verdict
  compares a SINGLE-FUNCTION candidate against a TU object that contains more than the function. It is
  **structurally unsatisfiable** for those functions however good the C is.
* **equal size** (`drawMainMenuModeSelectMenuOptions` 816=816, `func_8005905C` 1216=1216,
  `initControllerPakFileDeleteFlow`, `initMainMenu`, `initRaceTypeSelectMenu`): extent matches, so the
  difference is relocation or composition, and `relocation_order_equivalent` is False on every one.

**This does not by itself settle the tier question**, and it does not promote anything. What it does
establish is that for at least the size-mismatch subset the object-section flag cannot be the right
test — `function_boundary.certify` was written for exactly that, and on those functions it returns
`function_exact: true` with scope *"annotated function bytes and external call relocations only"*.

```bash
python3 eval/order_class_census.py --out eval/results/order-class-20260917.json
python3 eval/function_certificate_census.py --out eval/results/function-cert-20260917.json
python3 .cache/verify_ostask.py osSpTaskStartGo      # recompile + full certificate dump
```

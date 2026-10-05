# Byte-offset reconstruction experiment

Explicit byte views cleared the two previously noncompiling sprite bodies.
All four functions now compile under the target IDO recipe and pass the project's
strict candidate frontend, versus two of four for the baseline. There are still
zero byte-exact matches. This is a confirmed admission improvement on an exposed
development slice, not a recovered function or proof of full-function semantics.

## Concrete finding

The target computes two addresses from parameter `arg2` using an 8-byte stride:

```c
table = (arg2->unk4 * 8) + (u8 *)arg2 + 8;
cursor = (u8 *)arg2 + ((u16)arg3 * 8);
```

The original generated expressions omit the `u8 *` casts. Because the inferred
`T0` structure is 16 bytes, C pointer arithmetic scales those displacements by
16. A cursor-only repair actually emits a shift by seven and a trailing addition
of 128 bytes; the target emits a shift by three and an addition of eight bytes.
The byte-view treatment restores the latter instructions in both clipped bodies.
These are measured candidate and target instructions, not deductions from names
or ground-truth C.

Three local address views per body (`sp94`/`sp9C`, the descriptor cursor, and its
cursor-plus-eight subobject) also become `u8 *` instead of `void *`. One explicit
cast retains the generated command-word pointer field's existing type. No field
names, complete layouts, allocation extents, or pointer-field facts are installed.

This supports a specific reverse-engineering critique: inferred pointee layouts
are influencing address units too early. A source generator should preserve
observed byte arithmetic before choosing convenient structure views. Sharing a
larger inferred layout did not fix this in the preceding experiment.

## Bounded comparison

The inputs are the previous joint experiment's valid-syntax, source-independent
drafts for `drawMenuSprite`, `drawMenuSpriteClipped`, `drawMenuSpriteWithAlpha`,
and `drawMenuSpriteWithAlphaClipped`. The same four DEV functions are reused;
there is no held-out transfer claim. Public SDK headers, symbol/function identity,
and supplied TU build metadata remain assistance. Reference C bodies are excluded.

The three arms receive one fresh native compile per function, for 12 attempts:

| Arm | IDO compiles | Compiles plus strict frontend | Byte-exact functions |
| --- | ---: | ---: | ---: |
| Baseline: unchanged prior valid-syntax draft | 2/4 | 2/4 | 0/4 |
| Cursor syntax: local byte views and store cast | 4/4 | 2/4 | 0/4 |
| Byte units: cursor syntax plus both parameter-base casts | 4/4 | 4/4 | 0/4 |

The cursor-only bodies fail the strict frontend for assigning `T0 *` into byte
pointers. IDO accepts them but emits the wrong scaled address arithmetic. This
arm is an ablation, not an acceptable repair. Wrapper sources are unchanged in
all arms, and their object sections and relocations are equivalent.

The native run is `/home/grant/decomp/experiments/byte-offset-reconstruction-20260930-v2`.
Version one stopped before any compiles because a negative-control assertion
compared a deliberately altered source against the unaltered input. The assertion
was corrected before the frozen version-two run; no compiler trial was omitted.

## Remaining limits and decision

The corrected `drawMenuSpriteClipped` candidate uses a 232-byte stack frame against
the target's 160 bytes. The alpha candidate uses 216 bytes against 168. Instruction
scheduling, registers, and other differences remain. Stack-frame mismatch is an
observation, not proof of its source-level cause.

No full-function differential execution or game-domain validation was performed.
The finite arithmetic check validates only sampled address expressions. All exact
certificates are negative. The drafts also retain the previous experiment's
isolated-function prototype treatment; combined-TU interface consistency and a
final ROM build are untested.

Keep this as a narrow, retractable source hypothesis. It warrants a guarded
generalization that tracks address units separately from inferred object layouts,
with positive motivating fixtures and normal compiler/semantic gates. This
experiment does not authorize treating the drafts as recovered source.

Production solver code, wiring, main KB, and build-path functions were not changed.
Every compile, including the two failures, is logged in the private attempt DB;
the eight child attempts have explicit baseline parents. All artifacts remain
training-ineligible. There are no inference or evidence rows in that database.

## Evidence and verification

`portable/` retains the frozen probe, native receipts, source candidates, target
objects, candidate objects, diffs, environment identity, preregistration, controls,
and private attempt DB. It is ignored experiment output, not a committed dataset.
`probe.py` records the slice-specific edits and declines missing target motifs or
changed source patterns. It is deliberately not a production parser.

Verification on September 30:

- Positive emission and negative stride/source controls passed for both bodies.
- All 12 DB attempts, eight parent links, and source/target hashes were audited.
- All 10 compiled-object certificates were independently rechecked; none is exact.
- Unchanged wrapper object controls passed; the frozen probe hash matched.
- Existing address-unit and byte-view tests: **33 passed**.

Recheck the retained evidence with `python eval/results/byte-offset-reconstruction-20260930/verify.py`.
The audit result is saved in `verification.json`. A new native run can be made
with `probe.py --output <fresh WSL filesystem directory>`; it requires the retained
prior draft inputs and the configured native toolchain.

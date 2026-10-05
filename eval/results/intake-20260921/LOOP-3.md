# Iterations 3 and 4: wiring paid, and one iteration was spent on a misread message

| receipt | IDO compiled | IDO+frontend | byte-exact |
|---|---:|---:|---:|
| `smi` (end of LOOP-2) | 47 | 28 | 3 |
| `proto` (three actions wired) | **48** | **30** | 3 |
| `typedefs` (widened primitive set) | 48 | 30 | 3 |

**Iteration 3: +1 IDO, +2 frontend, 0 lost. Iteration 4: 0, 0, 0.**

## Iteration 3 — three passes that worked and were not actions

None of this was new repair logic. All three were complete, guarded, and reachable only from
`solver/modelrepair.py` — the MODEL path — so the deterministic intake route never called them.

| action | owns | previously reachable from |
|---|---|---|
| `m2c_dialect` | `(bitwise f32) x` | `repair_context.normalize` |
| `m2c_aligned_copy` | `M2C_MEMCPY_ALIGNED(...)` | `modelrepair.py:748` |
| `header_prototypes` | `implicit declaration of function` | `modelrepair.py:772` |

Gains: `alSynSetPan` (IDO+frontend) from `m2c_dialect`, `__osViSwapContext` (frontend) from
`m2c_aligned_copy`. `header_prototypes` fired on 13 states and converted **none**.

**`m2c_dialect` is the retest that paid.** It was built in LOOP-2 and deliberately left out of
`SEQUENCE` because its measured yield was 0: all three `bitwise` states also carried other syntax
errors. Those were cleared by `globals_variant` and `scalar_member_index`, and `alSynSetPan` became
sole-blocked by exactly the spelling it owns. **A zero-yield pass is worth retesting after the frame
moves; it is not worth retiring.**

**The trap that was declined.** The cheap reading of the `undeclared-function` bucket is "declare the
missing function". `M2C_BREAK` is the MIPS `break` instruction and `M2C_MEMCPY_ALIGNED` is a bulk copy;
neither has a ROM symbol, so a prototype compiles into a `jal` to nothing — a state measurably further
from exact, reported as a conversion. Same shape as the `unaligned` trap in LOOP-2. Pinned by
`test_a_macro_is_never_given_a_prototype`.

## Iteration 4 — a real fix aimed at a cause that was not there

`header_prototypes` fired 13 times and converted nothing, and every decline read **`requires one
primitive scalar header signature`**. Read literally, that says the signature was unsuitable. So the
guard's `primitive` set was the target: it held raw C keywords only, and the game is written in
`s16`/`u8`/`f32`, so `void enqueueSoundEffect(s16, s16);` canonicalises to `(('void',), (('s16',),
('s16',)))` and declined. All ten typedefs were verified in the project's own `include/PR/**`, and the
guard's teeth were left intact — `signature()` emits `*` as its own token, so `(('Actor','*'),)` still
declines.

**The fix is real and it could never have moved these states.** Firing went 13 → 56. Acceptance moved
0/0/0, and the fault histogram is **byte-for-byte identical** — every class count, every sole-blocker
count, every class-set-size bucket.

The actual cause, probed afterwards:

```
project_headers.declarations(repo, 'enqueueSoundEffect')    -> 0
project_headers.declarations(repo, 'drawMenuFillRectangle') -> 0
```

**No header declares either name.** The guard emitted one sentence for five distinct conditions, and
the condition that mattered is the one that sentence describes least.

### What was actually fixed, and the process lesson

`scalar_header_prototypes` now reports a reason per condition — absent declaration, too many variants,
complex declarator, conflicting signatures, non-primitive type — and carries `declarations` count. The
accept/reject decision is unchanged, so this **cannot** move acceptance and no frame run was spent
confirming that.

The lesson is mine to own: the loop's own rule says read the actual messages before writing code. I
read clang's and not the PASS's, and a generic decline message was enough to cost an iteration.
**Probe the decline, not just the diagnostic.**

## Iteration 5 — evidence gathered, and two of five are unreachable

`undeclared-identifier` is the top target: 94 states, **10 sole-blocking**, 5 of them a single error.
Probed, they are five different problems:

| state | the name | owner |
|---|---|---|
| `alSynSetVol` | `bitwise` on `(bitwise f32) _timeToSamples(...)` | needs the callee's return type — a prototype for a function no header declares. **Unreachable by this route.** |
| `__osCheckId` | `sp` in `(sp + sp3C)->unk1C` | m2c's undeclared stack frame. Still unowned; declined on purpose by `m2c_dialect`. |
| `waitCourseSelectRecordsClose` | `D_801121E0` | address-named; `lui $s1, %hi(D_801121E0)` names it in the relocation, so the address is a binary fact |
| `drawRaceSetupPlayerCountPrompt` | `gRaceSetupPlayerCountPromptText` | `globals_variant` reports it `unresolved` — no KB symbol row |
| `drawControllerPakDeleteConfirmPrompt` | `gControllerPakAreYouSureText` | same |

Two blockers found while gathering this, both worth recording rather than working around:

1. **`undefined_syms.txt` does not exist in the target repo.** `compile_recovery`'s `absolute-symbols`
   adapter reads it, so that adapter has no input at all here. This is also the most likely reason
   `tests/test_compile_recovery.py::test_compile_recovery_reapplies_absolute_adapter_to_later_drafts`
   fails — it synthesises one. **That failure is PRE-EXISTING**, bisected by reverting the widened
   primitive set in-process: it fails with the old set too. `solver/compile_recovery.py` was already
   modified in the working tree before this loop started, so it was left alone.

2. **`gRaceSetupPlayerCountPromptText` is declared only in `src/menu/race_setup/race_setup_ui.c`**, as
   `MenuGlyphScript gRaceSetupPlayerCountPromptText[5][0x34]`. That is the TARGET'S OWN SOURCE —
   `recovered` tier. It must not be read to drive a repair, so a type-correct declaration is not
   reachable from binary evidence.

### The candidate repair, and why it was not shipped unattended

The draft's use is pure address arithmetic with the scaling already baked in
(`(((var_t6 - var_v1) * 4) + var_v1) * 8) + gRaceSetupPlayerCountPromptText`), so
`extern u8 name[];` — an incomplete byte array — would compile and invent no size, element type or
field. It is derivable from the use alone.

It is also a **semantics-changing declaration**, and the risk is precisely the one this loop has twice
avoided: if the scaling assumption is wrong it produces a compiling-but-wrong candidate, which raises
the IDO count while moving away from exact, and the IDO metric alone cannot tell those apart. That
judgement belongs to the operator rather than to an unattended iteration, so it is written down here
instead of wired.

## Tests

`tests/test_intake_prototypes.py` (13) covers both actions, the macro trap, the header/SDK tier split,
and the per-condition decline reasons. Focused regression: **167 passed, 2 skipped, 1 pre-existing
failure** (the absolute-adapter test above).

Two of my own defects, both caught by the tests that assert a pass FIRES: `m2c_copy.propose` returns
one dict rather than a `(source, report)` pair, so the unpack silently yielded its keys; and the first
version of the typedef test string-parsed module source and `exec`'d it, which was replaced with one
that drives the real pass.

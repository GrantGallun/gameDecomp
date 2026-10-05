# Iteration 5 broke the ratchet, was reverted, and the loop stopped on its own rule

| receipt | IDO | IDO+frontend | byte-exact |
|---|---:|---:|---:|
| `typedefs` (baseline) | 48 | 30 | 3 |
| `undeclared` (merged clang's names) | **45** | **24** | 3 |
| `revert` | **48** | **30** | 3 |

**4 gained, 7 LOST.** Restored exactly: `revert` is identical to `typedefs` on every level, and no state
is still lost. First ratchet violation of the loop.

## What was tried, and why it looked right

`undeclared_identifiers_runner` takes the names it declares from `initial_verdict['stderr']` — cfe's
text, which stops at the FIRST error. That is precisely the starvation LOOP-1 found and fixed for
`globals_variant`, taking it from 5 of 200 states firing to 140 and the frame from 32 to 38 IDO. The
same defect in this module was never touched, `undefined_names` already matched both compilers'
wordings, and the change was three lines.

It is also the owner of the frame's largest class — `undeclared-identifier`, 94 states and the sole
blocker in 10 — and its own docstring records it COMPILING `drawRaceSetupPlayerCountPrompt` at 91.7 and
`drawControllerPakDeleteConfirmPrompt` at 95.3 once handed their names. Both of those did in fact
convert. The change was not wrong about its mechanism.

## Why it failed

**cfe's truncation was accidentally acting as a filter.** Naming only the first undefined identifier
usually named a real datum. clang names every independent blocker in one pass — including `bitwise`,
`sp`, `unaligned` and m2c's own temporaries — and this action declares whatever it is handed. So it
emitted `extern s32 bitwise;`.

`alSynSetPan` is the state `m2c_dialect` had won two iterations earlier **by lowering `bitwise`**.
`__osViSwapContext` is the state `m2c_aligned_copy` had won. Firing went ~13 → 159 of 200, and the
breakage outran the gains 7 to 4.

### The reasoning error, stated plainly

This change was chosen over a `extern u8 name[];` repair specifically BECAUSE it invented no
declaration — and "invents nothing" was treated as sufficient for safety. It is not. The pass invents
nothing; it acts on an input whose composition changed underneath it, and **a name is not a datum.**

The `globals_variant` precedent was a real parallel in mechanism and not in risk: that pass filters
names against KB symbol evidence before declaring anything. This one does not filter at all.

## What is kept

The frontend read survives as a **count only** (`frontend_names` per state, no behaviour change),
because it is the evidence the next attempt needs. Five tests pin the revert, including one asserting
the extra names are counted and NOT declared — the change is three lines and its reasoning still reads
well, so without a test it gets reintroduced.

## The loop stopped here, per its own pre-registered rule

Two consecutive iterations with zero IDO gain:

| iteration | change | IDO gain |
|---|---|---:|
| 2 `smi` | `scalar_member_index` | **+9** |
| 3 `proto` | three actions wired | **+1** |
| 4 `typedefs` | widened primitive set | **0** |
| 5 `undeclared` | merged clang's names | **0** (−3, reverted) |

Session total: **IDO 32 → 48, frontend 19 → 30, byte-exact 2 → 3**, one of which
(`writebackMenuRenderScratchBuffer`) is HEADER-ASSISTED via a reconstructed `include/game/**` header
and must not be quoted as a SOLVED capability number.

## What would resume it

1. **A whitelist of declarable names**, which is the actual fix iteration 5 needed: a name may be
   declared only with KB symbol evidence or an address-named `D_<hex>` form, explicitly excluding m2c
   dialect spellings and `var_*`/`temp_*` temporaries. `frontend_names` measures the gap per state.
2. **The `extern u8 name[];` tier decision** (LOOP-3). The draft bakes its own scaling into the address
   arithmetic, so a byte-array declaration invents no size or element type — but it is
   semantics-changing, and the IDO count alone cannot separate "compiles" from "compiles and is
   wrong". An operator call.
3. **`undefined_syms.txt` does not exist in the target repo**, so `compile_recovery`'s
   `absolute-symbols` adapter has no input and the `D_<hex>` states are unreachable for that reason
   alone. This is also the likely cause of the PRE-EXISTING failure in
   `tests/test_compile_recovery.py::test_compile_recovery_reapplies_absolute_adapter_to_later_drafts`,
   bisected as not caused by this loop.

## The finding worth more than any single repair

Four of the five iterations turned on the same structural fact: **a pass that already worked, which the
deterministic intake route could not reach or could not feed.**

| pass | previously reachable from | how it was found |
|---|---|---|
| `lower_bitcasts` | `repair_context.normalize` | LOOP-1 triage |
| `m2c_copy.propose` | `modelrepair.py:748` | LOOP-3 |
| `scalar_header_prototypes` | `modelrepair.py:772` | LOOP-3 |
| `undeclared_identifiers` | in `SEQUENCE`, fed cfe only | LOOP-4 (reverted) |

Enumerating every `propose`-style pass in `solver/`, and checking which the intake route can actually
reach and what it is fed, is likely worth more than the next several individual repairs. The fourth
entry is also the warning: reaching a pass is not the same as feeding it correctly, and the fix for the
fourth was not the fix that worked for the first three.

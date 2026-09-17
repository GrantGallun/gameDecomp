# The rename wall was not a rename wall

2026-09-17. Goal: *"make a pure register RENAME an instrumented residual instead of an uninstrumented
one."* The premise was measured and it is false. What is left is a different, sharper problem, and the
instruments that were supposed to own it own it even less than the premise assumed.

Tools added: `solver/regalloc_signature.Report.reordered` / `.renames` / `.order_only` (a measurement,
not a router); `eval/rename_census.py` (prevalence); `patterns/rules.py`
`same-shape-difference-is-a-permutation` (the hypothesis, run through the confirmation harness);
`eval/close_nearmiss.pairing_of` (the same split recorded on every closer run).

---

## The measurement, and how the premise fell

All five cases have **the same residual**, byte for byte:

```
 addiu    s2,s2,2
-move    s2,zero          target
 move    s3,zero
+move    s2,zero          candidate
 li       s0,0x80
```

The claim was that this is a pure register rename: `s2` and `s3` exchanged, neither the statement
permuter nor the register search able to move it. Two classifiers agreed with that reading, one did
not:

| instrument | says |
|---|---|
| `signals.analyse` | `regalloc=0 ordering=2` |
| `regalloc_signature.compare` | `gradient [0,2,2]`, `signatures {saved_order: 2}`, substitutions `{s2->s3, s3->s2}` |
| `patterns/ordering.classify` | `colouring` -- "the same instruction with its register exchanged" |

**Both readings are consistent with the two dumps, and neither is the truth.** `compare` aligns on
register-free `shape`, for which `move s2,zero` and `move s3,zero` are the same token; `classify` is
positional, so at each differing position it sees the same instruction with a different register.
Neither can see that the two dumps hold *the same two instructions*.

Two things settle it:

1. **Local arithmetic.** Within one shape-equal aligned block, the candidate contains the same
   instruction *texts* as the target, just at different positions. `reordered = common - same_position`
   counts instructions present on both sides at a different position; `renames = length - common`
   counts instructions that still differ once identical text is paired first. On all five:
   **`reordered=2, renames=0`**, and `reordered + renames == register_instructions` identically.
2. **Global evidence.** Every *other* use of `s2` and `s3` is byte-identical between target and
   candidate -- `addu t2,t0,s2` / `lhu a3,0x20(t2)` indexes the tile table, `addu a1,t5,s3` adds the
   offset to `y`. So `s2` holds the tile index and `s3` holds the offset in **both** objects. No value
   moved between registers. What IDO did differently is emit the two zero-initialisations in the
   other order.

The residual is an **emission-order difference**, and it is the class with no instrument at all.

## Neither owner can move it, measured

**The register search: 1,500 compiles, zero movement.** `eval/results/colouring-route-20260917/` --
the five cases through `close_nearmiss --force-regalloc`, budget 300:

| case | baseline gradient | best gradient | best label | exact |
|---|---|---|---|---|
| all five | `[0,2,2]` | `[0,2,2]` | `baseline` | **no** |

Not "did not converge": the gradient never moved. There is no register difference to reduce.

**The ordering pass fires and its output is invariant.** `rewrites.statement_order_rewrites` (the pass
that owns `ordering`) yields three variants on the best source -- `move statement i`, `move statement
offset`, `move statement tileIndex` -- and all three compile to a **byte-identical object** still
carrying the swapped pair, `gradient [0,2,2]`. Seven more hand-written orderings behave identically:

```
orig, offset_first_block, offset_then_tile_then_i, offset_first_flat, offset_before_block,
i_first, offset_after_i_in_block                        all -> gradient [0,2,2], same pair
offset = tileIndex = 0;                                 all -> gradient [0,2,2], same pair
offset = 1 - 1;                                         all -> gradient [0,2,2], same pair
offset declared with an initializer                     -> asm restructures, 97.280, worse
```

Ten source orderings and two spelling changes, one output. **Source statement order does not control
this emission order**, so the ordering rule's own premise -- "the target's order IS a source statement
order" -- is false for this class, not merely unhelpful.

## What does control it (the measured coupling)

The two `move`s are always emitted **s3-then-s2**, and the register assignment follows the emission:

| source | first emitted | holds | second emitted | holds |
|---|---|---|---|---|
| baseline (order correct) | `move s3,zero` | tileIndex | `move s2,zero` | offset |
| best candidate (registers correct) | `move s3,zero` | offset | `move s2,zero` | tileIndex |
| **target** | `move s2,zero` | tileIndex | `move s3,zero` | offset |

So in every reachable state the **first-emitted value holds the higher saved register**, and the target
needs the first-emitted value in the lower one. Fixing the registers (which the search did, via
`single_use:shouldDraw:inline`, gradient `[2,12,15] -> [0,2,2]`) flipped the emission order at the same
time. The two properties are anti-correlated on every source form tried, and the target needs both.

### Why, from uopt's own trace

`eval/results/rename-wall-20260917/adjsave_probe.py` reads `-zdbug:5`/`-zdbug:6` for the two ends of
the path. Both compiles reproduce the 2026-09-14 census -- saved colours are assigned in strictly
descending `adjsave` -- and the two values in question turn out to be ranked by it:

| compile | tileIndex | offset | emission |
|---|---|---|---|
| baseline | adjsave **14.4** -> colour 17 (`s3`) | adjsave **17.0** -> colour 16 (`s2`) | tileIndex, offset |
| best | adjsave **18.0** -> colour 16 (`s2`) | adjsave **17.0** -> colour 17 (`s3`) | offset, tileIndex |

In both, the **first-emitted value is the one with the LOWER adjsave**. Register assignment and
emission order are therefore locked together, and the target's combination -- emit the tile index
first *and* hold it in `s2` -- requires its adjsave to be both below and above the offset's. Inside one
block that is impossible, which yields the one falsifiable prediction this round produced:

> **the target's two zero-initialisations are in different basic blocks**, so the earlier block's
> definition is emitted in program order regardless of its colour.

Testing that needs the target's own u-code, which the tracing toolchain can only produce from a source
we can compile. Recorded in `patterns/catalog.py` as
`saved-colour-follows-adjsave-and-locks-emission-order`, `kind="review"`, `confirmed_on=[]` -- a
HYPOTHESIS on two compiles, which is exactly why it is written down as one and routes nothing.

## The refutation, through the project's own harness

`patterns/rules.py` now registers the reordering hypothesis in its strongest form -- *if the diff is a
permutation, the pass that owns `ordering` closes it* -- and `patterns/derive.py` judges it:

```
python3 -m patterns.derive --rule same-shape-difference-is-a-permutation \
    --functions drawRaceSplitscreenSelectOption2Frame,drawRaceSplitscreenSelectOption4Frame,\
drawCharacterSelectCoursePreviewPanel2,drawCharacterSelectCoursePreviewPanel6,\
drawCharacterSelectCoursePreviewPanel8
```

| claimed | declined | predicted | compiled | exact | confirmed |
|---|---|---|---|---|---|
| 5 | 0 | 15 | 15 | **0** | **NO** |

`NOT CONFIRMED: no case reached matches (claimed 5, predicted 15, compiled 15). Nothing to confirm.`

That is the refutation recorded where the project's process requires it: not a null accepted from a
failed guess, but a hypothesis that fired on its motivating residual and closed nothing. The rule
cannot be promoted, so no routing may be built on it.

## Reachability of the target state

`family_sweep.py` enumerates the same generator set breadth-first, with no beam and no model, from both
ends of the known path -- the baseline (order correct, registers wrong) and the best candidate
(registers correct, order wrong) -- and records for every compiled variant whether the dump has the
target's pair order **and** the target's register binding.

| start | depth | compiled | `gradient[0]==0` | target order | target register | **both (clean)** | exact |
|---|---|---|---|---|---|---|---|
| baseline `[2,12,15]` | 2 (exhausted) | 846 | 40 | 68 | 60 | **0** | 0 |
| best `[0,2,2]` | 3 (capped 2000) | 1502 | 398 | 50 | 648 | **0** | 0 |

Together **2,348 compiled variants, 438 of them with the instruction structure intact
(`non_register == 0`), and none in that set reaches the target's register/order combination.** 128
rows show both properties; every one is structurally broken (`gradient[0]` 8 to 11), where retyping the
index to `u8`/`u16` changes the scaling and the property test misfires on a different instruction pair.

That is a bound, not a proof: no composition of up to two mutations from the baseline, or three from
the best candidate, reaches the target state. It is not that the state is unreachable.

The forward search already did the strongly-related experiment: from the baseline, the register search
turned `[2,12,15]` into `[0,2,2]` by way of `single_use:shouldDraw:inline`, i.e. it found the register
half and lost the order half. The two properties have never been observed together.

## The 24 `reordered-only` functions, through the ordering pass

The census turned up 24 functions whose residual is a permutation, so the natural next move was to run
the owning pass over exactly that set: `eval/order_search.py`, depth 2, cap 90 compiles per function,
never ranking by score (the pass's docstring warns the byte score can fall even when the permutation is
right), acceptance by `att.exact` only.

| population | recorded | compiles | exact | best label |
|---|---|---|---|---|
| 24 | 24 | 481 | **0** | `baseline` for 23 of 24 |

Six functions hit the 90-compile cap, so those are truncated rather than exhausted; three
(`initMainMenuSceneModelRenderer`, `packFixedTransformMatrix`, `updateRacePlayerMode16AerialTrick`)
yielded zero variants -- the pass declines them. `updateRacePlayerMode16AerialTrick` is the load case
the catalog already records as a refutation.

So the ordering pass closes none of the class either, and `eval/close_nearmiss`'s missing `diff`
argument is **not** costing matches: wiring it would be a speculative behaviour change with a measured
yield of zero. It is left alone and the measurement is the receipt.

## Prevalence, beside the axes

`eval/rename_census.py` recompiles each function's best compiling attempt once and runs the same
comparison the search ranks on. Population: the **133** functions with a compiling, never-exact
attempt.

| pairing | n | of which `regalloc`-dominant |
|---|---|---|
| `renamed-only` (`renames>0, reordered=0`) | **95** | 47 |
| `reordered-only` (`reordered>0, renames=0`) | **24** | 2 |
| `both` | 13 | 11 |
| `no-register-difference` | 1 | 0 |

So the misreading is real but is **not** the main shape: most register residuals really are register
differences. The 24 `reordered-only` functions are where the register gradient is counting an order
difference and the ordering pass has been told the cause is colouring -- a double decline, with
`dispatchRacePlayerMode30Attack`, `updateRacePlayerMode16AerialTrick`, `bootThreadMain`,
`decrementRaceChallengeTimeLimit` and the five 99.936 siblings among them.

## The rule registry after this round

`patterns/derive.py` confirms a rule only when the oracle closes a case OTHER than the one the rule
came from. After this round the registry is unchanged in its confirmed set:

| rule | criterion | derivation case | confirmed_on | status |
|---|---|---|---|---|
| `ordering-store-reorder` | exact | Fstop | Fstop (99.999 -> 100.0) plus a recorded refutation on a held-out LOAD case | confirmed, scoped to stores with distinct operands |
| `target-linkage-static-inline` | compiled | addRacePlayerScore | **11 held-out** admission failures, none of them the derivation case | confirmed on ADMISSION, not on matches |
| `same-shape-difference-is-a-permutation` | exact | drawRaceSplitscreenSelectOption2Frame | **none** -- 5 claimed, 15 predicted, 15 compiled, 0 exact | **NOT CONFIRMED**, and therefore cannot change behaviour |

The third rule is the new one, and the refutation is its point: it is the strongest form of the
reordering reading, it fires on its motivating residual, and it closes nothing. No routing is built on
it, and the harness will refuse to let one be.

Scope: every fact added this round is IDO 5.3 `-O2` only -- uopt's colouring, IDO's emission order and
its `saved_order` numbering. Nothing here is claimed for a GCC target, and nothing reads a GCC object.

## What this changes

1. **Do not build the rename instrument the goal asked for.** There is nothing to instrument: the
   residual is not a rename, and `signals.analyse` already counted it correctly as `ordering`.
2. **Do not route these to `regalloc_search`.** Measured: the gradient does not move. `close_nearmiss`
   now records `pairing` on every run so a stalled search is legible instead of merely inconclusive.
3. **Do not route them to the ordering pass either.** Measured: ten source orderings, one output.
4. **The lever that is still missing** is a source shape that makes IDO emit the first of two saved
   register writes into the *lower* register. `single_use` inlining of the guard flipped the register
   assignment but flipped the emission order with it; nothing tried decouples them.

## Reproduce

```bash
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 eval/results/rename-wall-20260917/probe.py drawRaceSplitscreenSelectOption2Frame 31662"
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 eval/results/rename-wall-20260917/experiments.py drawRaceSplitscreenSelectOption2Frame 31662"
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 eval/results/rename-wall-20260917/ordering_probe.py drawRaceSplitscreenSelectOption2Frame 31662"
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 eval/results/rename-wall-20260917/adjsave_probe.py drawRaceSplitscreenSelectOption2Frame 11723,31662"
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 -m eval.rename_census --out eval/results/rename-census-20260917"
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 -m eval.order_search --out eval/results/order-search-20260917 --depth 2 --cap 90 --jobs 3"
```

## Logging

`eval.order_search` and `patterns.derive` log every compile they make (481 and 15 rows, with
`strategy = "order-search:..."` / `"derive:..."`), because those are the two drivers that could have
produced a match. The three offline census tools -- `family_sweep.py`, `rename_census.py` and
`adjsave_probe.py` -- compile with `conn=None` and write their per-variant receipts to the committed
JSON/`.md` files instead, with no knowledge-base rows; they enumerate scratch variants to bound a
search space rather than propose candidates to the pipeline. Every one of their compiles is in the
receipts: `family-sweep-*.json` carries `label`, `kind`, `parent`, `compiled`, `exact`, `score`,
`gradient`, `order`, `reverse` and `reg_s2` for each.

Match ratchet across the round: SOLVED **150**, byte-exact **216** (unchanged -- nothing here re-routes
or re-scores), attempts logged 49,962 -> **50,509**.

# Register steering from the confirmed 5.3 allocator model: first round

## Census of register-only functions (`census.py`)
16 functions whose best node's only residual class is register differences, each diagnosed from a traced
compile of its best source: 5 `selection`, 2 `ugen_temp`, 0 `blocked`, 3 with no wrong uopt range (the
differences are ugen temporaries, a later stage), 6 declined by the attribution (5 are near-identical draw-panel
functions), 1 trace failure. The first census run declined all 16: `verdict["dump"]` is the score summary, not
the object dump; the harness now rebuilds the best source's dump.

## Rules found while steering (catalog `uopt53-*`)
- Word locals get frame offsets -4, -8, ... in declaration order (3 of 3 orders), so ranges map to C variables
  without probing.
- An empty `if (!x);` adds two blocks to every range live across it; after a constant assignment it adds no read.
- A read inside a loop adds 10 to save.

## waitCourseSelectRecordsClose
Two constrained locals swapped callee registers. With every effect modelled (loop read +10 save, +2 blocks per
test for both ranges, step units, tie by live-range number) the model predicted the swap at k = 2 and 3 and not
at k = 1; the traced compiles agreed on all three. Score 97.39 -> 99.13 at k = 3, with no allocator difference
left. The remainder is ugen: a temporary numbered t8 where the target has t0, and two `addiu` in swapped order.

## Next
ugen is the next layer: temporary numbering and scheduling. It accounts for this function's remainder and for
the 3 register-only functions with no wrong uopt range.

## Round 2: the ugen half (after `ugen53-temp-fifo`)
`cc -S` showed our candidate's indirect call loading straight into t9 (`lw t9,44(s1); jal t9`), so the loop's
reload took the next FIFO register t8; the target's t0 means two more allocations first. Calling through the
global (`gCurrentMenuCameraObject->update()`) loads the object into t8, then t9, and the reload lands on t0, as
the target (`callback_variants.py`): 99.13 -> **99.348**. What remains is two independent `addiu` in the other
order: as1 scheduling. ugen emits `la s1` before `la s2` in every placement tried (`la_order.py`), and the
mutation stream has nothing for it (16 candidates, `finish_search.py`).

Path so far: 97.39 (search) -> 99.13 (allocator steering, 2 reads in the loop) -> 99.348 (temporary count via
the call spelling) -> as1 scheduling. Next layer: as1's scheduler, probed directly by assembling hand-written
ugen-style `.s` input.

## as1 probe by assembling ugen's .s: not faithful (dead end, recorded)
`cc -S` then assembling the `.s` with the same recipe differs from the direct compile (`as1_roundtrip.py`):
.text 0xd0 vs 0xc0; the direct compile forwards a just-stored value (`sw s1,0(s2); lw t8,0(s2); lw t9,44(t8)` ->
`lw t9,44(s1)`, another as1 copy-propagation skip) and fills a `blez` delay slot, the round trip does neither.
No flag restores parity (`as1_flags.py`: `-Wb,-O2/-O3`, `-Wab,-O2`, `-Wa,-O2`). The text form loses information
as1 gets from the binary form, so hand-edited `.s` cannot probe as1. Next: learn as1's scheduling from
(ugen `-S` order, object order) pairs of real compiles, which needs no edited input.

## Round 3: index form, population transfer, and the five 99.936 siblings
`solver/strength_inverse.py` (family `index_form`) undoes uopt's loop strength reduction. Attribution
(`attribution.py`): the index form with later uses read through the stored global is exact ON ITS OWN from the
pre-steering source; the allocator steering and the empty reads were not needed (a correct but unnecessary
detour). Population rerun (`../index-form-population-20260923/`): 15 exact vs 13 gated, 0 losses, and a function
it was never shown closed: **updateRaceUiResultsBannerWaitForInput** (index_form -> frontend_type), recorded
(375 byte-exact).

The five siblings at 99.936 (`move s2,zero`/`move s3,zero` in the other order): `cc -S` shows ugen emitting
`move s2 (tileIndex init), move s3 (offset), move s2 (tileIndex re-init)` for all five source orderings of the
initialisations in all five functions (`saved_order_fix.py`, 25 compiles). The order is decided inside uopt
(zero materialisations at range starts), not by source order and not by as1. Raising `offset`'s priority past
range 28 (4 reads) changes the colours, not the order (98.025). Consistent with catalog's "emission is the reverse
of colouring order" on 25 more compiles; not broken.

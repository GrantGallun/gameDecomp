# Enabling roots (`const_store_local`) for register search

Written 2026-09-15. The design changed once after a measurement, recorded below. The motivating-family run
of the final design had started, but no row existed when this was written.

## Question

Can register search reach the AerialTrick family without losing anything it already matches?

Motivating residual: the AerialTrick family, 30 pending siblings. `v = K; F = K;` gives the second constant its
own register and hoists that `li` into an earlier delay slot. From the m2c source, updateRacePlayerMode16AerialTrick
stalled at (1,1,1) after 300 compiles. After the edit `F = v`, search reached exact in 83 compiles, and Mode18 and
Mode19 in 88 each. The edit leaves the gradient unchanged and enables a `field_local` elimination.

## Designs tried, in order

1. **`const_store_local` as a beam family** (arm F, started then stopped). Measured on Mode16: still stalled at
   300 compiles, because a gradient-neutral candidate never wins a slot. Abandoned; arm F's partial rows are void.
2. **Enabling roots sharing the depth-1 frontier.** Measured on Mode16: stalled at (1,1,1) after 300 compiles,
   because the baseline's children used the budget first. Abandoned.
3. **Phased (final):** `regalloc_search.search(enable=True)`. The ordinary search runs with half the budget when
   enabling roots exist. Then each root no worse than the baseline gets its own search with the rest.

### Result of design 3, and design 4

Design 3 on all 30 (`aerial-enable-live/`): **6 exact of 30** (Mode16, 18, 19, 20, 21, 33), final count in
`analysis.json`. The prediction of ≥8 **failed**, so design 3 is not deployed. It was staged, and its hook and
suite passed.

Eight of the misses stalled at the same gradient, (0,6,8): Mode31, 35, 36, 44, 46, 47, 50 and 52/53. On Mode31
the missing step is a statement swap (`stateTimer += 0x16` before `updateTimer += 1`). Phase 1 finds that
improving move itself, but phase 2 applied the enabler to the ORIGINAL source. With the swap and the enabler
applied by hand, it was exact in 92.

**Design 4:** phase 2 takes enabling roots of phase 1's best source first, then of the starting source. Each root
must be no worse than the source it came from, and each is searched with whatever budget remains. Phase 1 is
unchanged, so the no-loss argument below still applies. Re-test: `aerial-enable-live-v2/`, all 30, same
settings, same prediction (at least 8 exact).

### Result of design 4 (deployed)

`aerial-enable-live-v2/`: **15 exact of 30**, against 0 reachable from m2c sources and 6 under design 3. Exact:
Mode16, 18, 19, 20, 21, 31, 33, 35, 36, 44, 46, 47, 50, 52 and 53. The prediction held. The re-staged suite passed
(2,741 tests) and so did the hook (Mode18 exact through staged `agentrepair.run`). Deployed as
`20260915-enabling-roots` at checkpoint 21469. Still missed: the `var_v1` siblings (13, 15, 41, 42, 45, 48, 49, 57),
where the edit makes things worse, plus Mode17, 34, 37, 39, 43, 51 and 56.

## Why no cohort A/B for loss

With the campaign's budget of 300, phase 1 is exactly the old search at budget 150, compile for compile. Every
exact across the budget-300 runs `regalloc-20260913/search-7`, `transfer-1` and `dominant-1` needed at most 97
compiles (138 exact, 0 above 150). So a loss requires a function whose first exact would fall at 151–300 compiles.
None has been observed. Functions without an enabling edit keep the full budget unchanged.

## Prediction

On all 30 pending AerialTrick functions (`progress-census-20260915/aerial-enable-live`, live generator set,
budget 300, untouched campaign sources): **at least 8 exact**. That is the 3 already shown plus a conservative
share of the 27 unmeasured.

## Decision rule

If the prediction holds, stage `constant_store_locals`, `enabling_variants`, the phased `search`, and
`enable=True` in `agentrepair._regalloc_search` as one amendment over the live frozen code. The amendment carries
only these edits, not the main tree's other generators or the diverse beam.

# Why exact progress stopped, and what is being patched (2026-09-15)

The campaign sat at 881 object-exact from 2026-09-14 16:55 through 2026-09-15 ~11:30, about 330 completed jobs.
Read-only analysis of checkpoint 20348 plus offline benches; nothing written to the campaign.

## Where the jobs went

`pipeline.log`, last 340 jobs before the analysis:

| profile | jobs | improved | exact |
|---|---|---|---|
| revalidate@bc9f7d51ce733708 | 238 | 0 | 0 |
| stack_layout | 48 | 5 | 0 |
| placeholder_recovery | 15 | 10 | 0 |
| model profiles (5 kinds) | 23 | 2 | 0 |
| everything else | 16 | 1 | 0 |

In the wider window of the last 1,500 jobs, register search had 93 jobs and 41 exact. The model profiles had 481
jobs and 5 exact.

## Register search only finishes a narrow band

Live receipts for every `regalloc_search` job to date, keyed by the residual it started from (the previous
receipt whose best source it searched):

| starting residual | jobs | exact |
|---|---|---|
| register-dominant, at most 2 other faults | 228 | 155 |
| more than 2 other faults | 16 | 0 |

That band is nearly used up: 22 unsearched at-most-2 nodes remain, and 17 of them are queued at band -1. So the
way to more exacts is to cut non-register faults until a node enters that band. Widening the search gate
alone does not help.

## What stands in the way (`census.py`, `diff_pass.py`, `classify.py`)

1,087 pending nodes. Dominant fault: register 648, structural 287, not compiling 75. 332 compiling nodes have
1 to 8 non-register faults. Those were recompiled in isolated benches and their aligned differences labelled with
branch offsets masked (`classes.json`). The recurring shapes follow.

- **Invented frame pads.** 8 functions have a `volatile u8 framePad[N]` exactly as large as the frame surplus.
  `stack_layout`'s own `stack_drop_unused` makes 7 of them exact in one compile (`probe_frame_pad.py`). They were
  queued behind the revalidate sweep, and the campaign made osViBlack and osViSetSpecialFeatures exact through
  `stack_layout` at about 11:45. Nothing to patch; 41 of the 50 frame and slot functions are queued.
- **Frame too large with nothing unused to drop** (drawMainMenuStaticBoardModel and similar). Relaxing
  `stack_layout.signals` to admit them was tried and measured: no proposal improved them. Reverted, not shipped.
- **Constant double store, AerialTrick family (30 pending siblings).** The target has
  `var_v0 = 0x400; player->stateTimer = var_v0;`, but m2c writes the constant twice. IDO then gives the second
  constant its own register and hoists that `li` into an earlier delay slot (target `nop`, candidate `li t1,0x400`).
  The campaign counts that as 3 structural faults, one more than the register-search band allows.
  - **Finding:** from the m2c source, register search stalled at (1,1,1) after 300 compiles. After the edit
    `F = K` → `F = v` it was exact in 83 (Mode16), and Mode18 and Mode19 were exact in 88.
  - **Mechanism:** the edit leaves the gradient unchanged and enables a `field_local` elimination that removes
    `var_v0` altogether. As a beam family it never wins a slot: with it in the generator set, Mode16 still stalled.
  - **Patch:** `regalloc_mutations.constant_store_locals` plus `enabling_variants`, and
    `regalloc_search.search(enable=True)`, which adds enabling edits no worse than the baseline to the first frontier.
    Measurement in progress: `aerial-enable-live/` runs all 30 with the live generator set.
- **Diffuse remainder.** `extra:`/`missing:` loads and `lui` reorders. No single shape covers more than about 20
  functions, and many are register or width differences that the rough labeller misfiles.

## Files

- `census.py`: pending nodes, fault tiers, lanes, the profiles tried on each current source → `census.json`,
  `pending.json`.
- `diff_pass.py`, `classify.py`: aligned differences and labels (`diffs/`, `classes.json`).
- `probe_frame_pad.py`: `stack_layout` proposals compiled on named functions.
- `probe_live_stack.py`: the frozen code's `stack_layout` inputs for one function. It showed the lane is not
  silently declining.
- `probe_aerial.py`, `probe_family.py`: register search from current or edited sources, with `--enable`,
  `--live-generators`, `--root const_store`.

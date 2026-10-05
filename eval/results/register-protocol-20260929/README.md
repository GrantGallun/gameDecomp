# Register protocol (`solver/register_protocol.py`): census of the round-2 register handoffs

2026-09-29. For each live range that `uopt_diagnosis` finds wrongly coloured, the protocol lists the levers
that could change its colour and checks each against the target, returning reachable, conditional or
unreachable. The mechanism comes from the IDO 5.3 trace model (99.9% of 11,310 SBK1 decisions). The
list of what sets a preferred register was read from the IDO 7.1 uopt decompile (n64decomp/ido,
`src/uopt/uoptreg1.c`, the `lu->reg` assignments): a parameter at entry, an out-mode parameter at exit,
a store to an outgoing argument, or a call argument in the same basic block. Nothing from the target's
reference source was used.

Run: `census.py`, 15 functions (every round-2 site-edit result with a register handoff). Traces and
per-function receipts are in `/home/grant/decomp/runs/register-protocol-20260929/`.

## Result

| verdict | functions |
|---|---:|
| conditional (no lever confirmed available, at least one possible) | 12 |
| no wrong range (register-only by the gradient, but only unattributed ugen renames such as t8->t7) | 2 |
| not register-only by the diagnosis | 1 |
| reachable | 0 |
| unreachable | 0 (after the fix below) |

- **spawnEndingCredits\*, 5 of 5 traced** (`TumblingSnowboard` has no wrong range). The wrong range is the
  `&gActiveMenuTask` constant: v1 where the target has a0. Preference is impossible because no call
  follows `createCallbackTask`, so no block where the range lives claims a0. Forbid-lower is impossible
  because the target holds nothing in v1 during the range. Coalesce is impossible. Only `parameter`
  remains possible, and the one standalone test of parameter reuse (`scratch idoprobe/param_reuse.sh`)
  did not produce a0. The family's register difference probably comes from the instruction-level
  structure. The target also uses one fewer ugen temporary: t7 where we use t8.
- **v0/v1 -> t6 or t1** (`updateEndingSlash*`, `randomNextObject`, `stepRaceMotion*`): the target
  register is a ugen FIFO temporary, so the lever is `inline`, making the value an expression temp.
- On the uopt-guided fixtures that were later matched by hand, `updateCharacterSelectRosterIcons` gets
  **reachable** (preference and forbid-lower both available). The protocol does separate cases; this
  frame just has none with an available lever.

## Bug found by running it (explained-or-broken)

The first census called `stepRaceMotionLoopingAnimation` and `...JointAnimation` **unreachable**, but
register search had made both exact by scalar coalescing. The levers assumed a fixed set of live ranges.
Added `coalesce` (a non-interfering range already holds the desired colour) and `inline` (the desired
register is a ugen temporary). Re-run: 0 unreachable. `tests/test_register_protocol.py` asserts that
these and the two uopt-guided `before` fixtures are never unreachable. The `coalesce` check still did
not flag stepRaceMotion itself: after the merge, the partner range's colour changes, which the check
does not model. `inline` is what keeps it conditional.

## What it is and is not

- An `unreachable` verdict is a model verdict about register edits that keep the instruction stream.
  It is not a proof about all C.
- On this frame the protocol does not yet pick a lever to apply. Most ranges are `conditional`. Its
  value so far is the negative information: for the credits family it rules out preference and
  forbid-lower, which points the next probe at structure rather than registers.
- It is not wired into register search or the campaign.

# Iteration 10: a regression acceptance could not see, and the ratchet that now can

| receipt | IDO | IDO+frontend | exact | frontend errors | states worse |
|---|---:|---:|---:|---:|---:|
| `widen3` | 53 | 34 | 3 | 3118 | — |
| `implicit` | 53 | 34 | 3 | **3134** | **12** |

## What was tried

`enqueueSoundEffect` and `drawMenuFillRectangle` are real ROM functions (KB `functions`: 0x80072138,
0x80046748) that NO header declares, so `scalar_header_prototypes` cannot reach the three states
sole-blocked on calling them. In C89 an undeclared call already IS `extern int f();`, and IDO compiles
it that way; writing the declaration out leaves the object unchanged by construction. `solver/implicit_extern.py`
did that, guarded against m2c macros (not in the ROM), read results (`temp_ret = __ll_mul(...)`, a
64-bit return `int` would truncate), and names a header already declares.

## What happened

It fired on 16 states and **added an error to each**: `a function declaration without a prototype is
deprecated`. The object claim was right. The claim that was never checked was the CHECKER's policy,
which rejects an unprototyped declaration exactly as firmly as an implicit one. There is no
object-neutral declaration for a callee no header declares — a prototype needs parameter types, and
those would be invented. Those three states remain unreachable by an invent-nothing route.

**Acceptance did not move, so the loop's ratchet did not trip.** The intake loop adopts a candidate on
`rank = (exact, compiled, score)`, so a candidate that is worse on the frontend but still compiles is
adopted on a TIE. Twelve states carried a degraded final candidate into every later pass, and nothing
reported it.

## What changed

1. `implicit_externs` is **withdrawn** from `SEQUENCE` and the registry, kept in source as the record,
   and a test asserts it stays withdrawn.
2. **`eval/quality_ratchet.py`** compares two receipts on acceptance AND per-state final frontend error
   count, and exits non-zero on either a lost state or a state that got worse. It fires on this
   regression (`RATCHET BROKEN`, 12 worse) and holds on a good iteration (`widen3`: 46 better, 0 worse).

## Retroactive audit of every kept iteration

"0 lost" had been claimed all session on acceptance alone, which is exactly the blind spot above. Rerun
under the stricter ratchet:

| step | frontend errors | worse | better |
|---|---|---:|---:|
| ferrorlimit2 → smi | 4045 → 3885 | 0 | 37 |
| smi → proto | 3885 → 3880 | 0 | 5 |
| proto → typedefs | 3880 → 3880 | 0 | 0 |
| typedefs → revert | 3880 → 3880 | 0 | 0 |
| revert → whitelist | 3880 → 3748 | 0 | 42 |
| whitelist → widen | 3748 → 3742 | 0 | 5 |
| widen → widen2 | 3742 → 3741 | 0 | 1 |
| widen2 → widen3 | 3741 → 3118 | 0 | 46 |

**Every kept iteration holds, with zero states worse.** The earlier claims were true — but they were
true by luck of what the passes did, not because anything was checking. Frontend errors fell
**4045 → 3118 (−23%)** across the kept iterations, most of it invisible to the acceptance levels.

## The rule this adds

**A ratchet on outcomes alone is not a ratchet when adoption is greedy on a tie.** It has to cover the
quality of the candidate being carried forward as well, or a pass can degrade every downstream input
while every reported number stays flat. From here the loop runs `eval.quality_ratchet` on every
iteration and reverts on a non-zero exit.

# Round 5: IDO 5.3 stack frame size (not confirmed; partial model)

Why: 111 of 145 uncovered `field:immediate` instances at unsolved best nodes are the frame size (50 functions).

| Model | Functions exact (of 1,819) |
|---|---:|
| H11 (pre-registered): align8(16 if calls + 4*saved_int + 8*saved_float + 4*memory_locals) | 920 (50.6%) |
| Amendment (after residual_check.py): outgoing area = max(16, highest stack-argument slot + 4) | 1,114 (61.2%) |

Established: leaf functions without memory locals 198 of 205; every residual >= +16 wrote a stack-argument slot
(calls with more than four arguments widen the outgoing area). Not established: the locals/spill part. Remaining
residuals +8 (380), -24 (102), -8 (97). Candidates for the next test: caller-saved values spilled across calls
(+8), and uncoloured isvar locals that need no stack home (-24, over-count). Verdict: not confirmed; no mechanism
built. Round outcome: 0 new exact, 0 confirmed rules.

## Round 7: the locals part (instrument failed; out of reach for now)
Held-out design (`locals_explore.py`: explore on TU-hash half A, keep half B for a pre-registered test). The
decomposition itself fails: frame == align8(outgoing + measured locals + saves) for 1,206 of 1,819, and for ZERO
functions that have a locals area. Counting accessed stack words cannot measure the locals area (arrays and
structs occupy words that are never individually accessed, and/or the assumed layout order is wrong). No
hypothesis was pre-registered, so half B stays unused. Next instrument, if revisited: `cc -S` prints `.frame` and
each local's sp offset symbolically, which measures the layout directly instead of inferring it. Round outcome:
0 new exact, 0 confirmed rules.

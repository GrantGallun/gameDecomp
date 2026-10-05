# ugen's expression temporaries: a FIFO cycle, hidden by as1's scheduling

7.1 claim (LLONSIT ido-decomp `src/ugen/reg_mgr.p`, read): FIFO free list t6, t7, t8, t9, t0..t5.

| Test on 5.3 matched targets (1,632 functions with temporaries, 27,371 allocations) | Result |
|---|---|
| FIFO scored in emitted order (pre-registered) | 61.9% (per-block reset 35.8%, lowest-free 25.0%): **not confirmed** |
| **Amendment**, order-free: allocated registers = first n of the cycle | **95.1%** of allocations; 1,013 functions exact |
| ...allowing allocated-but-never-emitted registers | all but **2** of 1,632 functions consistent (617 need skips; 3,694 skipped) |

Why the amendment: a synthetic probe showed ugen allocating t6 t7 t8 t9 while as1 emitted sll t6, sll t8, sra t7,
sra t9. `-Wb,-O0`/`-O1` do not stop as1's scheduling. The amendment was added after that probe, not after seeing
the census misses, and the pre-registered form is reported as not confirmed.

Skips: values computed straight into argument/return registers take no temporary (probe `args`: no skip); a load
feeding an argument left t6 unused (`args_then_store`). What reserves the skipped slot is open.

Two measurement bugs caught: the emitted-order walk (above), and a function-level rate that counted functions
with no temporaries as matches (73.5% reported, 62.1% true; the allocation-level 95.1% was unaffected).

Catalog: `ugen53-temp-fifo`. Next: pin the skip rule, then a temporary-shift mechanism: the cycle distance
between the target's and the candidate's register is the number of temporaries to add or remove before it.

## Skips explained (U3)
`cc -S` writes ugen's own assembly before as1 schedules and copy-propagates (numeric registers, pseudo-ops `mul`,
`la`). For `syn_h3(syn_a[a], syn_a[b], syn_a[a + b])` it shows t6 t7 t8 t9 t0 t1 t2 with no gap, where the object
skips t8: ugen wrote `mul $24,$2,4; move $2,$24`, and as1 folded the pair into `sll v0,a1,2`. A skipped cycle slot
is a temporary whose only use is a move into another register. One case, observed directly rather than inferred.
`-S` is now the way to see ugen's allocation order for any candidate.

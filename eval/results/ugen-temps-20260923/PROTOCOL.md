# Protocol: how IDO 5.3's ugen chooses expression temporaries

Written before `eval/ugen_temps.py` ran.

## 7.1 claim (LLONSIT/ido-decomp, `src/ugen/reg_mgr.p`, IDO 7.1 ugen, matched; read, not copied)
Temporaries come from a FIFO free list initialised t6, t7, t8, t9, t0, t1, t2, t3, t4, t5: allocation takes the
head, freeing appends to the tail. Reset at procedure entry (the summary also mentions basic blocks).

## Test (zero compiles)
Functions: every procedure in the 115-TU uopt trace census whose target dump exists
(`~/decomp/sbk1/nonmatchings/<fn>/target_object_dump_normalized.s`, the matched build's own code). Registers
uopt coloured in the procedure (level-5 colours) are excluded from the temporary pool. Walking the target
instructions in order: a write to a pool register that is not live is an allocation; its value is live until its
last read before the next write. Models, each predicting every allocation's register:
- **FIFO** (7.1): queue t6..t9, t0..t5 minus uopt registers; take the head; append a register to the tail at its
  last read; reset per procedure.
- **FIFO-block**: the same, reset at each basic-block leader (branch targets and after branches/jumps).
- **Lowest**: the lowest-numbered free register in the order t6..t9, t0..t5 (no queue).
Emission order is as1's, which schedules; a mismatch can therefore be the assembler's, not ugen's.

**U1 confirmed** if FIFO's agreement is >= 90% and at least 10 points above both other models. Otherwise the
model with the highest agreement is reported, with a sample of its misses, as a hypothesis for interventions.

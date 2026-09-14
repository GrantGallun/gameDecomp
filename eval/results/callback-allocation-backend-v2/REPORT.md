# Prologue-only residual from actual code-generator evidence

Twenty-four source identities were compiled on the parallel branch's 98.156 candidate, whose persistent registers already match the target. None beat the retained 98.511 score champion. However, [00.c](00.c) scores **97.979 and passes 76/76 structured semantic cases**, while matching every normalized instruction after the first thirteen listing lines. This is a useful structural frontier candidate despite its lower score. See [closest.diff](closest.diff) and [closest-replay.json](closest-replay.json).

The change is `type = (u16)(s16)type`. Its actual code-generator trace explains why it fixes the scratch-register sequence:

```text
andi t6,a1,65535   # ABI parameter normalization
move a1,t6
andi t7,a1,65535   # explicit source narrowing
move a1,t7
andi t8,a1,255     # selector
```

The assembler removes the redundant second normalization, but code generation has already advanced its temporary sequence. The selector and every downstream temporary now use the target registers. The surviving prologue differs because it keeps an initial move and normalizes into `a1` instead of retaining the target's normalized value in `t7` until the branch delay slot.

Evidence: [initial optimizer-output tree and later codegen passes](closest.dump), [emitted instruction trace](closest-ugen.log), and [full scores](scores.json). The input tree contains both the ABI normalization and the explicit signed/unsigned narrowing expression; the emission trace contains the two masks above. This is measured backend behavior, not a register-allocation heuristic.

The remaining targeted question is whether a source relationship can make the assembler preserve the second `t7` mask and discard the first, while deferring the assignment back to `a1` until after selector evaluation. The parallel agent is testing small local-value/assignment-order combinations. No additional broad identity expansion is warranted by this batch.

No diagnostic flags, compiler modifications, assembly substitutions or production source changes were used to claim any byte result. The only replayed candidate remains non-exact. The valid nondestructive diagnostic pipeline and exact allocated-image reproduction are documented in [the backend report](../callback-allocation-backend-v1/REPORT.md).

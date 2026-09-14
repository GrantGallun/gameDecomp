# Address-materialization compiler probes

This branch compiled **59 sources** with the unchanged production IDO recipe: 22 synthetic probes, nine transfers to the 97.454 callback, and 28 further transfers to the 97.454 and newly discovered 98.511 callback shapes. It found a compiler condition, but **no additional full-function improvement**. The 98.511 candidate from the independent structural branch remains the better source.

The direct synthetic field load emits:

```asm
lui v1,%hi(gCallbackTaskActiveListSentinel)
lw v0,%lo(gCallbackTaskActiveListSentinel+4)(v1)
```

Representing the address through a pointer/integer union or a pointer-to-integer widening/narrowing expression instead emits:

```asm
lui t6,%hi(gCallbackTaskActiveListSentinel)
addiu t6,t6,%lo(gCallbackTaskActiveListSentinel)
lw v0,4(t6)
```

See [direct probe](00.s), [integer representation probe](06.s), and [union probe](11.s). These observations isolate the address expression's compiler representation as one determinant of relocation folding. They do not imply the same instruction sequence survives optimization inside the full function.

When transferred to the full function, the compiler instead reuses the first materialized sentinel base across the guard and traversal. That removes the target's second address materialization and changes instruction scheduling/register allocation. Typed local intermediates do not prevent the reuse. Applying wider representations to the newer predecessor-only loop regressed its score; integer offset identities were inert. This provides direct evidence that local instruction recipes are context-dependent on IDO's global value reuse and register allocation.

The probes are retained in `solver/callback_address_hypotheses.py` as an experimental family, not promoted into the main candidate pipeline. Two focused tests cover probe construction, scope preservation, hard bounds and ambiguous reload refusal. No reference C, compiler flags, production source changes or build-guard changes were used. No new candidate beat the known champion, so this branch did not claim a new semantic-validated improvement.

Raw batches: [first scores](scores.json), [full transfers](../callback-inverse-address-v2/scores.json).

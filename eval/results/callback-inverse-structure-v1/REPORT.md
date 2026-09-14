# Predecessor traversal reconstruction

**97.454 → 98.511**, with **76/76 semantic cases passing**, zero inconclusive cases. Winner: `07.c`, production attempt 32322; replay in `replay.json`.

The missing sentinel address materialization was a source-structure issue. Keeping a copied `cur` pointer encouraged IDO to fold the address into the load. Expressing the loop through the predecessor directly restored the exact target opcode sequence and branch offsets:

```c
while (insertAfter->next != NULL) {
    if (insertAfter->next->priority < priority) break;
    insertAfter = insertAfter->next;
}
```

Merely moving the named cursor assignment into the loop reached only95.752. Removing that copied cursor is the meaningful difference. The resulting residual contains register names and equivalent jump-table relocation labels; the four-instruction target sentinel reload is now reproduced exactly.

This branch tested26 alternatives plus one repeated baseline: eight predecessor traversal forms, each also combined with delayed pool allocation and a positive allocation guard. All compiled. Delaying allocation and wrapping the tail in a positive guard regressed; the direct predecessor traversal alone is strongest.

`solver/callback_structural_hypotheses.py` implements the reusable bounded operator. Five tests pass. The recognizer requires a dead nonescaping cursor, verifies the predecessor's reaching sentinel initialization, and rejects unknown intervening effects, altered loop effects, volatile sources, and mismatched head fields. Compiler and semantic replay remain acceptance gates.

No compiler flags, assembly, build guards, or production sources changed. The candidate remains nonexact; similarity score is not the percentage of equal bytes.

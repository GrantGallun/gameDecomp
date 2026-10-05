# Retained range trace: `drawControllerPakFileDeleteConfirmOptions`

At coherent native checkpoint **28553**, the node was pending at **99.889** with retained attempt **157771**. Its source hash, both in the node and the native attempt, was `24a97e50a489ad6763031ecf1c23a43678e3a8de234578a6240f584614434a72`; the private copy was recomputed and matched. No reference or winning C source was read. The one ordinary, logged private recompile of that unchanged source reproduced **99.889**, compiled, passed the project frontend check, and was not object-exact. Private receipt **157772** has a checked `diagnostic-recompile` edge from 157771. Eighteen native ancestors were copied to preserve its real lineage.

Three further compiles of the **same source** produced ugen, uopt level-6, and level-5 traces. The private DB records their diagnostic outcome against baseline receipt 157772; the text traces are in `/home/grant/decomp/experiments/frontier-run-20260926/range-split-trace/`. Total compiler calls: **4**. The trace attribution reported two wrong ranges, both classified `split`, no non-register differences, and no unattributed differing register operands. See [TRACE.json](TRACE.json) for every attributed use, compiler line receipt, range metadata, and audit row.

The first observed difference is function-relative **0x28**, instruction index 10: target `li t0,0x80`, candidate `li v1,0x80`. Candidate uopt attributes that destination to local-kind range **4**, node **992**, offset **-6**, coloured **v1**. The compiler's direct source-line record maps it to retained source line **30**, `var_v1 = 0x80;`. The complementary difference is at **0x30**, index 12: target `li v1,0x80`, candidate `li t0,0x80`; range **7**, node **960**, offset **-8**, coloured **t0**, directly mapped to line **31**, `var_t0 = 0x80;`. The relevant retained first arm is:

```c
u16 var_v1;
u16 var_t0;
if (gControllerPakMenuState.state == 2) {
    var_v1 = 0x80;  /* line 30: candidate v1, target t0 */
    var_t0 = 0x80;  /* line 31: candidate t0, target v1 */
    if (gControllerPakMenuState.confirmChoice == 0) {
        var_v1 = 0x100;
    } else {
        var_t0 = 0x100;
    }
}
```

Instruction index **11** is the same `bnez t7,3c` in both objects, between the differing loads. Index 12 is its delay slot. Both normalized streams have **90** instructions, and their *only* differing indices are **10 and 12**. Exchanging those two candidate instruction texts in a read-only diagnostic comparison makes the entire parsed stream equal to the target. This is an exact observed instruction-order discrepancy; the swap was not compiled as a C proposal.

The next attributed occurrences of these ranges already agree: range 4 is `v1` in both objects at index 14 (**0x38**, source line 33), and range 7 is `t0` in both at index 16 (**0x40**, source line 35). Each range has one disagreeing target-register vote and five agreeing votes. Thus the observable disagreement is the *initial pair of loads* before the conditional arms reconverge; it is not a demonstrated long-lived wrong colour. The trace's explicit internal split events involve range IDs **54→58**, **58→59**, and **52→60**, not 4 or 7. Here `split` is the diagnosis category for mixed target votes within a candidate range, not proof of the target compiler's exact split operation.

The target uopt live-range graph and its source are unavailable from this experiment. Candidate blocks, source lines, and register votes establish the two differing instructions and their candidate locals, but do not establish why the target assigned its first two loads in the opposite order. A paired first-arm assignment-order effect is a directly testable hypothesis; a lifetime change is **not** established. No edit or compile probe of that hypothesis was made, and no production, frozen, KB, or live-campaign state was changed.

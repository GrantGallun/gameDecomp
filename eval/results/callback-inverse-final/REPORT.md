# Callback inverse-compiler swarm

Callback similarity improved from **97.454 to 98.511**. All **76 semantic replay cases passed**. Exact byte verification remains false; this score is not the percentage of identical bytes.

Three agents ran 198 compiler experiments: 59 address-materialization probes/transfers, 112 joint source combinations, and 27 predecessor traversal experiments, including repeated baselines. The successful change removes the copied traversal cursor:

```c
while (insertAfter->next != NULL) {
    if (insertAfter->next->priority < priority) break;
    insertAfter = insertAfter->next;
}
```

This restores the missing sentinel address-building instruction. The normalized instruction sequence and branch offsets match the target. Remaining differences concern register allocation and equivalent jump-table symbol labeling; the exact certificate remains the authority.

The guarded, bounded transformation is integrated into `eval/differential_repair_pilot.py` through `solver/callback_structural_hypotheses.py`. A normal deterministic search independently reproduced 97.454 -> 98.511 in 56 candidate evaluations, passing all 76 cases and using zero model tokens. The address and joint experimental families remain outside the normal search because they produced no additional gain.

Regression validation: 179 tests passed across the new families, source attribution, semantic cache, candidate frontier, residual generators, pilot, and transition policy.

Evidence:
- `../callback-inverse-integrated/summary.json`: normal pipeline result.
- `../callback-inverse-integrated/createCallbackTaskPreservingArgs.search.json`: full search receipt.
- `../callback-inverse-structure-v1/replay.json`: independent winner replay.
- `../callback-inverse-structure-v1/REPORT.md`: successful structural investigation.
- `../callback-inverse-address-v1/REPORT.md`: synthetic compiler condition and failed transfers.
- `../callback-inverse-joint-v1/REPORT.md`: joint search; additional batches in corresponding v2/v3 directories.
- `createCallbackTaskPreservingArgs.c`: retained candidate.
- `remaining.diff`: normalized residual from the independently verified structural winner.

No production source integration or whole-ROM exactness is claimed. The result demonstrates that verification can guide useful source reconstruction, while the remaining register choices still require discovering a source form that induces the desired compiler allocation.

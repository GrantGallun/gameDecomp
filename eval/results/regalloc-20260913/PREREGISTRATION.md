# Register-allocation signature search — preregistration (2026-09-13)

**Cohort (frozen before running).** The file is `cohort.json`, exported read-only from checkpoint 14495.
- 78 `regalloc_only` pending functions: residual faults are register allocation only.
- 145 `regalloc_plus_le2`: register allocation plus at most two other faults. This cohort is not part of this test.

**Hypothesis.** A large share of the frozen campaign's +-0 register-allocation residuals are source shapes that
enumerable, meaning-preserving mutations reach. The mutations are local integer types, commutative operand order,
declaration order, inlining a temporary, and statement order. The search ranks variants by the
`solver.regalloc_signature` gradient, not by byte score. No model is involved.

**Procedure.**

    python -m eval.regalloc_probe search --cohort cohort.json --out search-1 --workers 2 --budget 200 --beam 3 --depth 3

Each function gets at most 200 compiles, in an isolated workspace with a private attempt log. The object oracle decides
exactness, and nothing is written to campaign state.

**Primary measure.** The number of the 78 that become object-exact.

**Success threshold:** at least 15 of 78 exact. Below 5 exact counts as refuted for this mutation set and goes in
`memory/hypothesis-graveyard.md`. Between 5 and 14 is partial: report it and analyse the residual failure modes.

**Secondary measures.**
- Functions whose gradient improved without becoming exact.
- Which family produced each exact match.
- How the remaining signatures are distributed.

**Development disclosure.** makeFixedRotationXY was matched by hand during development: its `s16 temp_v0` local
declared as `s32`, found with `regalloc_probe probe`. That observation motivated the `local_type` family. It is
reported separately and excluded from the threshold count.

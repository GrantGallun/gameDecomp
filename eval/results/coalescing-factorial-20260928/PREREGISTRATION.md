# Coalescing wiring and exposed-case replay

This is a development regression, not a yield estimate. The two functions were
already used to identify coalescing: `stepRaceMotionLoopingAnimation` and
`stepRaceMotionLoopingJointAnimation`. Roots, objects and header context come
from the frozen `evolvability-trial-20260928/bundle-1`. Reference function bodies
and prior winning C are not inputs. Preserve header-assisted labels.

First compile each root and every proposed coalescing variant through the strict
native compiler adapter (limit 12, so at most 13 calls including the baseline).
Both motivating roots must offer a certified exact candidate with passing
frontend checks. Record all calls and source lineage, including failures.

Then run a 2 x 2 comparison on those same frozen roots, seed 0, budget 128:

| Selection | Existing vocabulary | With scalar coalescing |
|---|---|---|
| Current production gradient | production | production_coalesce |
| Experimental offspring ranking | evolvability | evolvability_coalesce |

Shared settings: enabling roots, optimizer keys, key cost 0.14, 2% random audit,
certificate rechecks before expansion, beam 3, depth 4, diversity off. Evolvability
uses preview 64, probes 2 and exploration probability 0.2. Root compiles, failures,
probes and rechecks all count. Report actual calls, key calls, effective budget
and any overshoot; this inherited cost model does not equalize wall time.

Primary result: certificate plus frontend exactness. Secondary: best full-listing
gradient among actual compiles. Preserve per-arm results, the production-baseline
summary, an evolvability-baseline summary (family effect with exploration), and a
production-coalescing-baseline summary (selection effect with the larger vocabulary).
No residual-routing changes and no campaign/KB/ledger promotion are part of this run.

The generator is deliberately narrower than the earlier regex prototype: leading
plain scalar locals, no later declarations, loops, jumps, labels or address
escapes. Uses must occur in textual order with standalone initial assignments.
Integer width/sign changes are explicitly labeled hypotheses. This is not a
general liveness analysis or proof of semantic preservation.

Fresh-cohort evaluation remains separate: freeze the generator and current
unmatched candidate census first, stratify assistance/residual and prior exposure,
and run all four arms on the same functions. The old 553 count is a snapshot.

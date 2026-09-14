# Callback dispatch and allocation lifetime experiments

The standalone index-inlining candidate improved **94.723 to 95.007** and passes
**76/76 differential cases** (`25.c`, `replay.json`). It removes the index local
and reads the already decremented global count at the pool lookup. It is not
the overall champion: the independently recovered sentinel-loop shape scores
97.454, and index inlining on that shape regresses to 96.709.

The experiment tested 34 initial alternatives, 18 compositions with the
sentinel-loop candidate, and 6 dead-selector/index storage combinations. Pointer
register qualifiers, selector widths/casts, decrement spellings, and pointer
scope changes were inert. Sharing a counter local across switch cases made
code generation substantially worse. Reusing the dead selector for the pool
index peaked at 97.383 on the sentinel branch.

This rules out these particular bounded storage forms; it does not establish
that register allocation cannot be corrected. Remaining assembly evidence ties
the residual to index/new-task allocation and the sentinel-address expression:
the candidate index occupies `v1`, moving the new-task pointer to `t0`, while
the target keeps its new-task pointer in `v1`. The sentinel branch also has a
different address-materialization instruction count, handled by the other agent.

The experimental library `solver/callback_dispatch_alternatives.py` remains
separate from the production search. Three tests verify bounds, source scope,
effect qualifiers, and decrement-before-read ordering. No compiler settings,
build guards, reference C, or production translation units were changed.

Evidence: `scores.json` (first batch),
`../callback-finish-dispatch-v2/scores.json` (18 compositions),
`../callback-finish-dispatch-v3/scores.json` (6 lifetime combinations).

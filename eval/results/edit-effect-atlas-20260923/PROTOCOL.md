# Protocol: does a C edit's effect follow the lines the residual is attributed to?

Written before `locality.py` ran.

## Idea
Map C to diffs: every recorded search edge pairs a C edit (parent -> child source) with a diff effect; the
compiler's own line records (`source_attribution`) tie each residual instruction to a C line. If edits that touch a
residual's line improve far more often than edits that do not, a search can spend its compiles where the faults
are -- the missing ingredient when a function carries many independent faults.

## Test (no compiles)
Edges: every parent->child edge in the recorded population worlds of 2026-09-22/23 whose parent compiled, is not
exact, and has a verified attribution bound to its source. For each edge: the parent lines the edit changed
(line diff), and the parent's residual lines (attributed candidate lines of differing aligned instructions).
- **L1.** P(child improves | edit touches a residual line) vs P(improves | edit touches none).
  **Supported** if the first is >= 2x the second with >= 200 edges on each side.
- **L2.** Of the improving edges, the share that touched a residual line (how much a locality-first order would
  keep), and of all edges the share that did not (how much compile budget it would free).
Deduplicated by (function, parent source, child source) as in eval.machinery_card.

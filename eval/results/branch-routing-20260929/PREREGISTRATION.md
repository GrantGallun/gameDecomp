# Branch-shape routing: compile the variants the campaign never reached

PRE-REGISTRATION, written 2026-09-29 before any compile.

## Question

`../structural-residual-20260929/` found that `solver.branch_shape` emits 203 variants on the best states
of 75 unsolved functions, of which 202 were never compiled in either ledger. None of the 75 ever had a
branch-shape family attempt, and register search (the only caller of `branch_shape.families`, via
`regalloc_mutations.variants`) never ran on 58 of them. Is that a routing loss, meaning the variants
would have helped if reached, or are the gates firing on states they can't repair?

## Frame and method

The 75 functions in `../structural-residual-20260929/branch_fire_new.json` with at least one
never-compiled variant, each at its best compiling non-exact attempt in either ledger. Compile every
variant once (no search, no chaining), scored by `workspace.score` and logged to
`/home/grant/decomp/runs/branch-routing-20260929/trial.sqlite`. No model, no reference source.

## Measures

Exact (`repair_complete`). Improved: a variant with lower `site_edits.gradient` than the function's
re-scored baseline. Broke: a variant that doesn't compile (a mechanism defect: branch-shape
rewrites should never emit C the frontend rejects).

## Predictions

- Exact: 1–5 of 75.
- Improved: 20–40% of functions.
- Broke: < 10% of variants. More means the generators emit invalid C on states unlike their
  motivating ones, and that's the defect to fix before any routing change.

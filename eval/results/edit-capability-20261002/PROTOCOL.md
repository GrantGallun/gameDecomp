# Edit capability ladder (2026-10-02)

Question: **which kinds of C edit can the solver model make, and how much information does it need
before it makes each one?** Measured, not inferred: the byte-exact oracle grades every attempt.

## Design

Start from functions that are already byte-exact from **binary-derived context only**
(`context-ablation-20260924` BINARY arm, `rows_binary*/` with `status == exact`). These are this
project's own C, not reference source, so nothing held-out enters a prompt.

For each edit class, apply ONE known perturbation to the function body and keep the case only if the
perturbed source **compiles and is not exact** (perturbations the compiler normalizes away are counted
as `invisible`, never silently dropped). The model then gets the perturbed function plus its
declarations at one information level and returns a full definition. Exact = masked object dump equal
to the original's (the same mask the ablation used).

Classes. *Equivalent* ones keep C semantics (so only compiler-shape knowledge can recover them);
*semantic* ones change meaning (the asm must be read):

| class | kind | perturbation |
|---|---|---|
| commute | equivalent | `a OP b` -> `b OP a` for + * & \| ^ |
| cmp_mirror | equivalent | `a < b` -> `b > a` |
| if_invert | equivalent | `if (c) {B}` -> `if (!(c)) {} else {B}` |
| stmt_swap | equivalent* | swap two adjacent independent simple statements |
| temp_return | equivalent | `return E;` -> `tmp = E; return tmp;` |
| const | semantic | integer literal N -> N+1 |
| arith_op | semantic | `+` <-> `-`, `<` <-> `<=`, ... |
| arg_swap | semantic | swap two adjacent call arguments |
| drop_stmt | semantic | delete one simple statement |
| cast_width | semantic | change a cast's width/signedness |
| decl_width | semantic | change a local's declared width/signedness |

(*stmt_swap is equivalent modulo pointer aliasing.)

Information levels, cumulative except S:

| level | model receives |
|---|---|
| L0 | declarations + perturbed function, "does not match the target" |
| L1 | + target assembly (normalized object dump) |
| L2 | + unified diff target vs current assembly |
| L3 | + the line the mismatch originates from |
| L4 | + the edit class in words |
| S  | line + class, **no assembly** |

Model: `gpt-oss:20b` (the pipeline's model), think=low, temperature 0.2, one sample, fixed seed.
Pilot: 6 cases per class. Outcome per attempt: `exact` / `compiled` (score) / `not-compiled` /
`no-definition`; also whether the original line was restored verbatim.

## Limits stated in advance
- Mostly small functions (the clean-exact pool is small-biased); site-local single edits only.
- One sample per cell: a capability *floor*, not pass@k.
- L3/L4 hand the model the planted site and class. That models a perfect localizer/classifier, so
  read L3-L4 as "what the model can do IF a tool tells it this", not as unaided capability.

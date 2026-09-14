# The unknowns ledger

A work queue built on what is *not* known, rather than on how close a score is.

## Why

`eval/triage.py` and `solver/signals.py` both classify residuals **after** a
compile: they need a diff. So every function that does not compile scores 0 and
is invisible to the queue. Measured 2026-09-01, that is 1,823 never-attempted
functions plus ~140 drafts whose compile fails — most of the binary sits in a
blind spot, indistinguishable from each other and from hopeless.

Unknowns are enumerable **before** a compile, from the target assembly and the
m2c draft alone. That gives a pre-compile tractability metric, and one test the
current queue cannot express:

> **If a function has zero free unknowns and still does not match, either a
> pass is broken or an unknown is not enumerated.**

That is "explained-or-broken" (CLAUDE.md) applied to the solver instead of the
miner. Given that four separate capabilities turned out to exist-but-unwired in
a single day — the call graph, `structgen.render`, `rewrite_do_while`,
`globals_layout` — that list is expected to be long, and it is the point.

## Prior art, and what we take from each

This framing is not novel. The theory is published; the gap is that nobody has
applied it to **byte-exact** matching.

**Superset Decompilation / PGSD** (Liu, Sun, Gilray, Micinski, arXiv 2603.28002,
2026) — a monotonic annotated relation store, ambiguous interpretations retained
as parallel candidates with provenance, resolution deferred to a final selection
phase. Roughly 60% of their nodes lift unambiguously; ~40% carry competing
candidates. **Take:** deferred commitment as an architectural rule, and the
observation that their selection phase is *error-directed greedy search with the
compiler as oracle* — which is what `eval/zero_token_harvest.py` already does by
accident. **Leave:** the ℕ[X] provenance semiring. A set of evidence row ids per
candidate is the 90% version at 5% of the machinery; revisit only if we need to
count derivations rather than cite them.

**BinSub** (arXiv 2409.01841, 2024) and **Retypd** (Noonan et al., PLDI 2016) —
types as *capability sketches*: what a value can do, not what it is named.
Record fields as `(offset, size): τ`, with **separate load (covariant) and store
(contravariant) parameters** because one polarity alone loses information.
BinSub reaches Retypd's precision (type distance 1.67 vs 1.68) with bi-unification
instead of pushdown saturation — 63× faster on 1,568 functions. **Take:** the
capability set and both polarities; the simplification strategy if we ever solve
rather than enumerate. **Leave:** full subtype inference for now — we are
enumerating unknowns, not solving for types.

**TIE** (Lee, Avgerinos, Brumley, NDSS 2011) — a lattice with upper and lower
bounds. Our "unknown is the default" invariant is TIE's lattice bottom, arrived
at independently. **Take:** vocabulary and the discipline of distinguishing "no
constraint" from "contradictory constraints".

**Abductive error diagnosis** (Dillig, Dillig, Aiken, PLDI 2012) — compute
queries identifying exactly what information an analysis is missing. This is the
formal name for the ledger, and the piece the decompilation papers above do not
address: PGSD retains ambiguity but its abstract does not describe ranking
candidates or enumerating what is still open.

**Not applicable, and worth stating:** DecLLM (ISSTA 2025), AutoDecompiler
(arXiv 2606.16162), Context-Guided Decompilation (arXiv 2511.01763) all target
*recompilable* or *functionally equivalent* output and measure compiler errors
or re-executability. The community tools that do target byte-exact — decomp.me,
decomp-permuter, m3c — have no constraint layer; m3c is m2c plus permuter brute
force. Neither half does both.

## The schema

An **unknown** is a variable the binary does not pin.

**No new table.** The `inference` tier already has exactly this shape and holds
0 rows — the fifth capability found built-but-unused in two days, after the call
graph, `structgen.render`, `rewrite_do_while` and `globals_layout`. It was read
as "the KB thesis is unexercised"; it is better read as *the tier was built to
store answers, and what the solver needs is a store of questions*. Same table,
inverted use:

```
inference(id, kind, subject, value, confidence,
          origin, status, created_at, retracted_at, retraction_reason)
inference_support(inference_id, evidence_id)
inference_depends(inference_id, depends_on_id)
```

| column | use here |
|---|---|
| `kind` | see KINDS below |
| `subject` | `Fdrumsoff:param0`, `global:0x80110918`, `callee:alLink`, `Ffor:block3` |
| `value` | the capability sketch, JSON — see CAPABILITIES |
| `status` | the lattice — see LATTICE |
| `origin` | the resolver that can discharge it, or the pass that raised it |
| `confidence` | **only** meaningful on `constrained` rows ranking rival candidates |
| `retracted_at` | a candidate the compiler killed; retraction is already modelled |
| `inference_support` | provenance. This is CLAUDE.md rule 4, already enforced |

Two properties fall out of `subject` being free-form text, and both are correct
rather than convenient:

- **Function-scoped subjects carry the function** (`Fdrumsoff:param0`), so no
  `func_addr` column is needed.
- **Global subjects deliberately do not** (`global:0x80110918`). A global's
  layout is a program-wide fact, which is exactly `globals_layout`'s finding —
  a function sees 62 field offsets on the globals it touches while the rest of
  the program sees 166, 2.7× more. One row, shared by every function that
  touches that address, is the right shape and the flywheel in miniature.

A row with `status != 'free'` and no `inference_support` entries is a bug.
`confidence` must never promote a `constrained` row to `known`: a hypothesis
does not get to change behaviour.

### KINDS

Derived mechanically from the draft plus target assembly. Each maps to a
resolver that either exists, exists-but-is-unwired, or does not exist:

| kind | resolver | state |
|---|---|---|
| `undeclared_type` | `solver/typedecl.py` | wired |
| `undeclared_global` | `miner/globals_layout.py` | **exists, unwired** |
| `callee_signature` | `solver/protostore.py` (83 verified) | partial |
| `struct_layout` | evidence + `solver/structgen.py` | wired |
| `loop_form` | `rewrite_do_while` / `loop_shape_rewrites` | wired |
| `field_extent` | — | **unpinnable** |
| `field_name` | — | **unpinnable** |
| `branch_shape` | — | **none** |
| `jump_table` | — | **none** |
| `register_allocation` | — | none (global, compile-only) |

### CAPABILITIES

Per BinSub, what the subject can *do*, not what it is called. Every entry is
mechanically observable — from `evidence` for the binary side, from the draft's
syntax for the source side:

```json
{
  "accessed": [{"offset": 112, "width": 4, "signed": null}],
  "loaded":   true,          -- evidence.is_load = 1   (covariant)
  "stored":   true,          -- evidence.is_load = 0   (contravariant)
  "dereferenced": ["m"],     -- draft writes x->m->n   => m is a pointer
  "subscripted":  ["stack"], -- draft writes x->m[i]   => m is array/pointer
  "called":   false
}
```

`dereferenced` and `subscripted` are the direct fix for the defect measured on
2026-09-01: `typedecl` declared every member a scalar, so of 25 plans that fired
only 4 compiled, and 7 of the 21 failures were `Subscripting a non-array` or
`Selector requires struct/union pointer`. Under this schema those are not two
special cases but two capabilities the sketch already has to carry.

Our `evidence` rows are `(base, offset, width, signed, is_load)`. That is
BinSub's `(n₀,n₁): τ` with polarity, already mined, 72,845 rows. No new
extraction is needed for the binary half of the sketch.

### LATTICE

Four states, not two. The distinction between the last two is the one that
decides whether a queue entry is work or noise:

- `known` — pinned by evidence. Cites the rows. Becomes a constant, and a
  constant is shared: `SchedulerState`'s layout resolved once helps every
  function that touches it. This is the flywheel, made explicit.
- `constrained` — bounded but not unique. Multiple candidates, each with cites.
  PGSD's ~40% case. Carry all of them; let the compiler select.
- `free` — nothing currently pins it, but something could. **This is the work
  queue.**
- `unpinnable` — no binary fact could ever pin it. Field names; whether two
  adjacent 4-byte slots are one array or two scalars; signedness where only `lw`
  appears. Recorded so the ledger does not accumulate questions with no possible
  answer, which is how a backlog becomes noise.

## Ranking

Ascending by count of `free` unknowns, tie-broken by instruction count.

A function with **zero** free unknowns should be solvable *right now* by
existing passes. Those form the falsification set described at the top: every
member that does not match is a defect in our machinery, not a hard function.

Deliberately **not** ranked by count alone — one `branch_shape` unknown in a
300-instruction function is worse than twelve `struct_layout` unknowns. Weight
by resolver state (`none` ≫ `unwired` > `wired`), not by cardinality.

## What this is not

Not a solver. Register allocation and instruction scheduling are global
functions of the whole body — that is why the permuter exists and why
`uopt-save-model-can-steer-source-edits` came back REFUTED at 50–58% against 50%
chance. Those variables are testable only by compiling, and they belong in the
ledger as `register_allocation` with resolver `none` rather than as something to
be inferred.

Not a replacement for the oracle. The ledger says what to look for. Byte-exact
object comparison remains the only authority on whether we found it.

## Validation, before it is allowed to steer anything

A ledger that enumerates nothing looks exactly like a codebase with no unknowns
— the fifth rule in CLAUDE.md, and the failure mode this project keeps
rediscovering. So, in order:

1. **Fires on known cases.** On `Fdrumsoff` it must emit exactly one
   `undeclared_type` for `PlayerCommandState` with `accessed=[(112,4)]`, status
   `free`, resolver `typedecl`. On `releaseRelocatableHeapBlockMetadata` it must
   emit `undeclared_global` for `gRelocatableHeapUsedBlockCount` (width 2 from
   evidence, joined via `symbol_addrs.txt`) *and* `subscripted` on
   `gRelocatableHeapFreeBlockStack`.
2. **Zero-free set is non-empty and honest.** Rank the 174 leaves already swept.
   The 5 that matched must show zero free unknowns. Any *other* function showing
   zero free unknowns and no match is a defect to explain — that is the payoff.
3. **Ground truth checks the ledger, never feeds it.** SBK1 is 100% matched, so
   `eval/validate_evidence.py` can confirm a `known` row is right. It must not
   supply one.
4. **Contamination line unchanged.** `symbol_addrs.txt` joins on *address*;
   widths come from evidence. Its `// size:0x4` annotations are the decomp
   team's curation and are not read. `include/game/**` is not read.

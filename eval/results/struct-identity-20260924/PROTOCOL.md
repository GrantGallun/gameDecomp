# Protocol: cross-function struct identity from binary dataflow, scored against the reference headers

Written 2026-09-24 before `identity.py` and `score.py` existed. Context: `type-flywheel-20260924` (the type layer
is the lever; about 172 of 278 SOLVED depend on the reference team's headers). Goal: recover, from the binary
alone, which pointers in different functions point to the same struct, and each struct's observed layout.

## The pass (`identity.py`, binary only)
Input: the built ELF's functions, disassembled and decoded exactly as `miner.evidence.disassemble`. No source, no
symbols beyond function extents and call targets (which are binary facts).

Type variables: `P(f,k)` (param k of f, k < 4), `R(f)` (f's return), `G(a)` (the object at absolute address a),
`D(t,o)` (the pointee of the pointer stored at byte o of t), `E(t,o)` (an object embedded at byte o of t).
Abstract register values: `(t, delta)` (a pointer to t plus a byte delta), `hi`/`abs` (lui/addiu addresses, as in
the miner), or unknown.

Per function, a linear walk (as `miner.evidence.resolve_bases`) with these effects:
- entry: a0..a3 hold `(P(f,k), 0)`; move/addiu/lui/ori track as in the miner; addiu on `(t,d)` adds to delta.
- `lw rd, o(r)` where r holds `(t,d)`: record an access; rd := `(D(t, d+o), 0)`. From an `abs` address A:
  rd := `(D(G(A), 0), 0)`.
- other loads and stores through `(t,d)`: record access (t, d+o, width, signedness, load/store, int/float).
- `sw rs, o(r)` with rs = `(x,0)` and r = `(t,d)`: unify `D(t,d+o) = x`. With r = `abs` A: `D(G(A),0) = x`.
- `jal f`: for each a_k holding `(x,0)`: a **call edge** `P(f,k) ~ x`; holding `(x,d)` with d != 0: `P(f,k) ~ E(x,d)`;
  holding `abs` A: `P(f,k) ~ G(A)`. After the call (delay slot included), caller-saved registers are
  clobbered, and v0 := `(R(f), 0)`.
- `jr ra` with v0 = `(x,0)`: `R(f) = x`.
- Branch merges: a branch target keeps only sp, gp, and registers defined only in the entry block (before the first
  branch, jump or branch target) and never redefined in the function; a0..a3 count as defined at entry. Everything
  else is dropped, as in the miner.
Unification is union-find with structural merge: merging t and u merges `D(t,o)` with `D(u,o)` and `E(t,o)` with
`E(u,o)` for every o. Accesses are attached to roots at the end, where `E(t,o)` contributes to t at +o.

## Variants (call edges are the only difference)
- **A, no call edges**: intra-procedural plus stores and loads only.
- **B, all call edges as equality.**
- **C, hub-capped**: call edges into a callee parameter are dropped when that parameter receives arguments from
  at least H distinct caller functions (a generic helper: the void-pointer problem that Retypd handles with
  subtyping). H is chosen on the FIT half from {2, 3, 4, 6, 8, 12, 16, 24}, maximizing B-cubed F1 subject to
  precision >= 0.90.

## Scoring (`score.py`; the reference is the answer key only, never an input)
Labels: `eval.ground_truth.load` signatures. `P(f,k)` is labeled S when the reference declares param k of f as
`S *` and S is a parsed struct. Items: labeled slots whose function is in the ELF. Split by sha256(function name)
parity: even = FIT, odd = CHECK. Reported numbers are CHECK.
- **B-cubed** over labeled items grouped by union-find root: precision_i = |group(i) with label(i)| / |labeled in
  group(i)|, recall_i = |group(i) with label(i)| / |label(i)|, averaged. Baseline (every slot alone): precision 1,
  recall = mean 1/|label|.
- **Layout agreement**: for each root with a majority label S (at least 2 labeled items), its merged int
  (offset, width) observations checked against `ground_truth.flatten(S)`. Agree = the width is in the set at that
  offset, or the offset lies inside a field of that size (bulk copies and unions, as in validate_evidence).
  Reported as a share of observations; uncheckable ones are counted apart and never treated as agreement.
- **Gain**: for labeled slots in a multi-function group, the number of distinct offsets known for the group that the
  function itself never touches (what a shared header adds over one function's own evidence).
- **Fires test**: at least one labeled struct with >= 10 slots must reach recall >= 0.5 on CHECK under the chosen
  variant; otherwise the pass is declining silently and the numbers are not read.

## Reading (fixed now)
- **Gets us somewhere**: CHECK B-cubed precision >= 0.90 and recall >= 0.50 (against the baseline recall), and
  layout agreement >= 0.95. Then: the downstream test (headers from the groups, assembly-only m2c redrafts of the
  unsolved population, compile and exact rates against the same drafts without the header), a protocol of its own.
- **Partial**: precision >= 0.90 but recall < 0.50: identity edges are too few. Report which edge kinds contributed.
- **Null**: precision < 0.90 in every variant: dataflow identity over-merges; subtyping is required.

## Amendment A1 (2026-09-24, after the first FIT/CHECK run of A, B, C and before any CHECK run of D)
First run (`score.json` v1): A precision 1.0 / recall 0.170 (baseline 0.170); B 0.523 / 0.448, layout agreement
0.16; chosen C24 0.955 / 0.199, layout 0.80. The fires test FAILED (no struct with >= 10 slots at recall 0.5),
so those numbers are not read as a verdict.
FIT-only diagnostics (`diagnose.py`; labels used to understand edges, never as input):
- Generic helpers that merged many types (`setCallbackTaskCallback`, `addRenderCallback`, heap helpers) never
  dereference the parameter. Keeping a call edge only when the callee dereferences P(callee,k): type-specific callee
  parameters kept 19, mixed-label ones kept 2 (threshold max offset >= 0); at >= 0x10: 19 and 1.
- Callback co-passing (a call whose arguments include a function address g and a pointer x): P(g,0) has x's label in
  110 of 111 labeled cases.
**Variant D** = A plus (i) call edges into callee parameters that the callee itself dereferences with some direct
access at offset >= T, T chosen on FIT from {0, 0x10, 0x20} (precision >= 0.90, maximum F1); plus (ii)
co-passing edges P(g,0) ~ x for every call site with exactly one pointer argument x (delta 0) and one or more
function-address arguments g. The same CHECK metrics, fires test and reading apply; D replaces C as the chosen
variant only if it is selected on FIT by the same rule.

## Amendment A2 (2026-09-24, instrument bugs found on FIT after A1's run; before the next CHECK run)
A1 run: chosen D16, CHECK precision 0.977, recall 0.245 (baseline 0.170), layout 0.959, fires test passed
(RaceIntroEffectActor 0.556): partial. FIT inspection (`inspect_chain.py`) found two bugs:
1. **Stale argument registers**: a call site reads a2/a3 still holding the caller's own entry parameters although the
   call did not set them, fabricating edges to callee params the callee never takes, and making co-passing sites look
   like they carry several pointers. Fix: a call edge for argument k is emitted only if the CALLEE reads a_k before
   writing it (in the callee's linear instruction order: its binary arity).
2. **Duplicate pointer arguments** (`f(x, g, x)`) counted as two pointers in co-passing. Fix: deduplicate.
Everything else in D (threshold selection on FIT, metrics, reading) is unchanged.

## Amendment A3 (2026-09-24, after A2's CHECK report; motivated by context-ablation DEV failures, not by labels)
The linear walk drops every non-entry register at each branch target (inherited from miner.evidence), so accesses
through pointers that m2c tracks fine are lost: in context-ablation's DEV set, 20 BINARY compile failures were fields
the walk never observed. Replacement: forward dataflow over basic blocks, where a block's entry state is the meet
(values equal in every predecessor) of its predecessors' exit states, iterated to a fixed point; facts are recorded
in a final pass over the converged states. Stack slots are handled the same way. The variant selection, split, metrics
and reading are unchanged; the result is reported as v3 next to v2 (A2), not in place of it.

## Amendment A4 (2026-09-24, after v3; motivated by binary-types-capability compile failures, not by labels)
Table accesses were lost: `addu rd, base, index` with a known global address and an unknown index made rd unknown, so
fields read through `table + i*N` were never observed (m2c then leaves the pointer `void *`). Now rd holds an element
pointer into the table at that address; loads/stores through it are recorded on the element node ("A", G(addr)) at
their offset, and `lw` through it yields D(("A", G(addr)), off). Pointer-typed bases are unchanged. Re-scored as v4
next to v3 with the same rule; the capability generator uses v4 only if CHECK precision and layout agreement do not
fall.

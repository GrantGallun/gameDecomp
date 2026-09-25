# Protocol: do the reference's own corrections of m2c drafts cover the unsolved population's residuals?

Written 2026-09-24, while `pairs.py` was collecting and before `mine.py` existed or any association was computed.

## Question
The rule-by-rule loop (guess a rule, protocol, synthetic compiles, mechanism) finds one IDO shape rule per round.
The finished SBK1 decomp holds about 1,500 confirmed corrections of the kind the pipeline needs: m2c's draft of a
function (A), the matching reference C (B), and the compiler's residual for A. If residual classes predict which
C-shape change turns A into B, those associations are mechanism candidates found all at once. This measures whether
they would reach the functions the pipeline is actually stuck on, before any mechanism is built.

## Split (fixed in `pairs.py` before any row was read)
- **Scored population**: the 207 functions of the restart round-3 run (`restart-round3-20260923/rows`). Their
  reference source is never read; the name exclusion runs before any `src/` lookup.
- **Sealed**: every function under a `dev`, `heldout`, `cluster` or `panel` key in `eval/sets/*.json` (594 names).
  Excluded from mining.
- **Mining**: every other workspace function whose compile root (`.compiler-target.json`) defines it exactly once
  after inlining local `#include "x.c"` fragments: 1,558 functions.

## Pairs (`pairs.py`, copied workspaces, no KB writes, reference source kept on the WSL side)
- `ref.c`: the function's compile root with every other definition reduced to its prototype.
- `draft.c`: the same context with the definition replaced by m2c's `base.c` definition. These drafts carry the
  reference's context by construction, which is legitimate only for mining functions, and it factors types out, so
  the residual is m2c's shape gap.
- **Harness validity**: a row is usable only if `ref.c` is byte-exact **modulo relocation symbol names** (the
  target names static data, `D_800E1A80`; the rebuilt TU fragment relocates against `.rodata`/`.data`). Exact-raw
  and exact-mod-reloc are both reported. Found on the 16-function smoke run, before this protocol: 11 raw-exact,
  3 differing only in relocation names.

## Residual features (identical code for mining drafts and population best nodes)
Computed from (target dump, candidate dump) with relocation operands masked (`%hi(X)`/`%lo(X)` -> `%hi(R)`), the
unified diff regenerated with GNU `diff -u`:
1. `S:<class>`, the branch-layout census structural class (`branch-layout-20260924/census.classify`).
2. `R:<class>`, every distinct class from `eval.mechanism_roadmap.classes(diff, None)` (field:*, opcode:a/b, extra:*,
   missing:*).
3. `F:bigger` / `F:smaller`, the candidate frame versus the target (first `addiu sp,sp,-N`), when they differ.
Population residuals come from each unsolved function's best node (`raw_diff` applied to its target dump, as in the
census). A function whose masked dumps are identical has no residual and is left out on both sides.

## C-shape edit families (draft definition -> reference definition, shapes only, never names)
Counts over the comment- and literal-masked body: `if`, `else`, ternary `?`, `return`, `for`, `while` (not a
do-while tail), `do`, `goto`, labels, `switch`, `case`, `break`, `continue`, local declarations, `register`,
`volatile`, casts to a type, compound assignment, `++`/`--`, statements (`;`), subscripts, `->`.
A family is a feature plus a direction: `else+` means the reference has more `else` than the draft.
Identifiers, types' names and field names are never features. This is the contamination line from 2026-09-21.

## Association
Over usable mining pairs whose draft compiles and is not exact modulo relocations. For residual feature r and
family e: n_r pairs with r, k with r and e, P(e|r) = k / n_r, lift = P(e|r) / P(e|not r).
**Enriched**: n_r >= 10, k >= 5, P(e|r) >= 0.30, lift >= 2.

## Positive controls (the instrument must FIRE on residuals whose fix is already known)
Last night's confirmed rules predict:
- C1: `S:count` is enriched for at least one of `else±`, `?±`, `return±` (select/path-count and return-tail rules).
- C2: `S:loop-shape` is enriched for at least one of `for±`, `while±`, `do±`, `goto±`, `label±`.
If either control fails and its residual has n_r >= 10, the instrument is suspect: the coverage below is
reported as descriptive only, not as a verdict. If n_r < 10, the control is untestable and says so.

## Coverage of the scored population
U = unsolved population functions with a residual. For u in U:
- covered-any: at least one of u's residual features has an enriched family.
- covered-all: every residual feature of u has one.
- unseen: features of u with n_r < 10 in mining (listed; no association is possible).
Also reported with TU-excluded mining (associations recomputed per u without mining pairs from u's compile root).

## Reading (fixed now)
- **Promising**, meaning build mechanisms from the top associations next: covered-all >= 40% of U and
  covered-any >= 70%.
- **Null**, meaning shelve the direction: covered-any < 30%.
- Otherwise **mixed**: build only for the classes with the largest covered demand.
An association is a candidate hypothesis about a fix, not a fix. Promotion still requires a mechanism, a fire test
on a real residual and the paired population rerun with 0 exacts lost (catalog rule: `confirmed_on` empty means
hypothesis).

## Reported regardless
Draft compile rate and harness validity rate, both by size (t_len < 50 / < 150 / larger); the ten strongest
associations; unseen features by demand.

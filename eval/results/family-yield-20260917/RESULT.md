# Where the machine time actually goes, and what the reference's git history says about it

2026-09-17. Prompted by: *"look at repos git histories to see how they reasoned about an answer."*
The reference submodule (`external/snowboardkids-decomp`, 4,015 commits, **2,258 of them naming a
match**) carries the reasoning of the people who finished this game, including commits that record
MEASURED levers and measured NON-levers. This mines both sides: their conclusions, and a census of
where our own search budget goes.

Tool: `eval/family_yield.py` (pure query, no compiles).

---

## 1. Our mutation families: 17,800 attempts, 3 exact matches

Per family, counted from the `attempts` table by the family named in the strategy label:

| family | attempts | compiled | **exact** |
|---|---:|---:|---:|
| `local_type` | 5,403 | 5,403 | **0** |
| `stmt_move` | 5,719 | 5,210 | **0** |
| `commutative` | 3,479 | 3,451 | 1 |
| `stmt_order` | 1,586 | 1,586 | **0** |
| `decl_order` | 1,194 | 1,194 | 1 |
| `field_local` | 154 | 154 | 1 |
| 9 more families | 265 | 253 | 0 |

**12 of 15 families have zero exact closures in the entire corpus.** The three that do are
`commutative` (1), `decl_order` (1) and `field_local` (1).

### The caveat that stops this being a licence to delete them

Exact closures are the wrong sole measure of a *family*, because a function usually needs several
levers composed and a family can be a necessary stepping stone while never landing the final object.
The reference's own record makes that concrete and is in tension with a naive reading of this table:
`patterns/imported_ido.py` already carries `ido-statement-order-is-an-allocation-lever`, where **24
permutations of four independent sibling assignments moved positional word mismatches from 271 to 397
of 422** with sequence, count, frame and stack homes held constant. Statement order *is* a real lever
on the gradient. It has simply never been the thing that landed the final object in 1,586 tries.

## 2. The sharper distinction: diff-read order works, blind permutation does not

The two order families together: **7,305 attempts, 0 exact.** But this project has closed functions with
statement order — `Fstop` (99.999 → exact by reordering five stores into the target's order) and
`func_8005B49C`. In both, the permutation was **read off the target's diff**, not searched.
`patterns/catalog.py` `ordering-residual-states-the-statement-order` already says why: *"the permutation
that closes these functions is a COMPOSITION of several swaps, so no single variant from the baseline
can be the answer, and 14 of them firing is consistent with none of them being right."*

So the family is not inert; **the search formulation is**. `patterns/rules.py:StoreOrderRule` already
does the right thing for one case — read the target's emission order off the diff and apply it in one
step — but it is scoped to stores with one base register.

**Capability unlock #1: generalise diff-read permutation past stores.** That replaces a
8,891-attempt / 0-exact blind search with a computed answer, and the reference's 24-permutation
experiment is independent evidence that the target's emission order is a legitimate source order to
write back.

## 3. Where our budget goes, and where the matches come from

By strategy root, the whole 51,095-attempt corpus:

| root | attempts | exact | exact/attempt |
|---|---:|---:|---:|
| `faultsearch-d2` | 2,478 | 2 | 0.0008 |
| `zero-token-m2c-harvest-m2c` | 1,726 | 3 | 0.0017 |
| `repair-pair` | 1,260 | 1 | 0.0008 |
| `campaign-intake` | 1,158 | **157** | **0.136** |
| `m2c-semantic-seed` | 470 | 5 | 0.011 |
| `dag-pipeline-census-root` | 336 | 4 | 0.012 |
| `agentrepair-root-reverify` | 266 | 6 | 0.023 |
| `symbols` | 200 | **48** | **0.240** |
| `authorized-target-history-recovery` | 201 | 46 | 0.229 (recovered tier) |
| **`zero-token-m2c-harvest-typedecl`** | **154** | **18** | **0.117** |
| `two-lane-leaf-m2c` | 16 | **13** | **0.813** |

The top three budget sinks — `faultsearch-d2`, `zero-token-m2c-harvest-m2c`, `repair-pair` — are
**5,464 attempts for 6 exacts (0.11%)**. The proven producers are `campaign-intake` (13.6%),
`symbols` (24%), and **`zero-token-m2c-harvest-typedecl` at 11.7% on only 154 attempts**.

**Capability unlock #2: the typedecl path is the best-motivated admission route by a factor of ~100
over the failing search**, which independently justifies rounds 5–7's focus on it — and says the
budget currently going to `faultsearch-d2` and `repair-pair` is the cheapest thing to move.

## 4. What the reference recorded that we have not mined

Commits that name a lever or a measured cause, with their claims:

| commit | claim |
|---|---|
| `5c8d6f2c` | **IDO does not reliably fold consecutive constant left-shifts**: `(x << 3) << 5` emits two `sll` while `(x << 1) << 1` collapses to one — so shift decomposition must be read off the target, and it is a STRUCTURAL residual, not an allocation one. Also: `s32 v[1]` **demotes a local to a true memory variable**, freeing its register for whichever variable lost the previous round, at the cost of function-wide uopt conservatism. |
| `9834bdf5` | Records a **statement-order allocation lever** from the `drawMenuSpriteFixedScale` campaign. |
| `fd27a480` | A local initialised to a literal is **folded away before webs exist**, so a "constant carrier" can never recolour a constant — measured across `s32/u32/s16/s8/u8/char`, top and mid-function, and grafted onto a dead local: **every variant produced the same object**. Also: subscripting by a variable blocks a fold-and-hoist that a pointer walk invites, and vice versa. |
| `d5dcbb8e` | Diffing **`cc -S`** (ugen's output, final register numbers) isolates one changed decision where diffing objects shows dozens of shifted rows. And for the symmetric `v0/v1/a0/a1` group: declaration order, statement order, nested scopes, expression grouping, separate carrier variables and line layout were **all measured inert** — only the order *within* each pair follows source. |
| `520d327b` | **Do not trust a permuter score**: the permuter's `compile.sh` invokes `cc` without `asm-processor` and never strips `.mdebug`, so its scorer reads debug collateral — a 43% apparent improvement on a candidate whose `.text` was instruction-words-identical. |

Two of these are **already contradicted or superseded by our own stack**, and I am not importing them as
actionable:

* `cc -S` is weaker than what we have. Our `solver/uopt_trace` reads uopt's own `-zdbug:5/6` trace, which
  is the layer *above* `-S` and reports the colouring decisions directly. Do not downgrade.
* The permuter-scoring warning does **not** apply to us: `build.sh` runs `objcopy --remove-section
  .mdebug` before the dump, and `regalloc_signature` compares normalized `.text`. Worth recording as a
  hazard we have already avoided rather than a change to make.

The two genuinely new, unimplemented levers are **shift-decomposition** and **one-element-array
memory-forcing** (`s32 v[1]`). Neither is in `patterns/imported_ido.py`'s 10 entries nor in
`regalloc_mutations.py`'s families.

## Next steps, in order of evidence

1. **Generalise diff-read permutation past stores** (unlock #1). Highest value: it converts a
   8,891-attempt/0-exact blind search into computation, and `StoreOrderRule` is the template.
2. **Move budget off `faultsearch-d2` / `repair-pair`** (0.08% each) toward the typedecl admission route
   and intake paths (11.7% / 13.6%). Reallocation, not new machinery.
3. **Implement `s32 v[1]` memory-forcing and shift-decomposition** as generator families, each with a
   fire test on its motivating residual — the reference measured both, and our oracle has not.

## Reproduce

```bash
python3 eval/family_yield.py                      # by strategy root
python3 eval/family_yield.py --families           # per mutation family
git -C external/snowboardkids-decomp log --pretty="%h|%s" -i --grep="lever"
git -C external/snowboardkids-decomp show 5c8d6f2c -- DECOMPILATION_LEARNINGS.md
```

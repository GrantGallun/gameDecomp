# Can each repair mechanism perform its action? Measured, four fixed, +1 exact

**368 byte-exact (was 367), SOLVED 272 (was 271).** `dispatchRacePlayerMode07CourseObject` closed the moment
`single_use` stopped inlining into a comment. No model calls; one independent recompile recorded through the
ratchet (`inventory-receipts.json`).

## The question

Codex's capability envelope declares, per mechanism, what it *should* be able to do. This measures what each
one *did*, on every recorded application in the four population arms (about 9,500 distinct parent/child
edits over 224 functions, `eval/machinery_card.py`). For each application the parent's compiled object is
compared with the child's: did the mechanism **break** the build, change the source but not the object
(**no-op**), reach **exact**, or **act** (and then: improve, stay clean, or introduce new residual faults).
Nothing is declared about what a mechanism is for; its effect signature is measured.

## What the machinery does

| Behaviour | Mechanisms (share of their applications) |
|---|---|
| Performs as intended | `residual_evidence` (89% of actions improve), `owner:layout` (layout axis shrinks in 60% of actions, 2 exacts), `field_local`, `owner:frame_padding` (100% clean), `owner:drop_mask` (2 exacts), `owner:global_load_signedness` |
| Cannot act (object unchanged) | `commutative` 89% of 1,711, `decl_order` 76%, `truth_test` 81%, `register_storage` 87%, `compound_assign` 78% |
| Acts wrongly (uncompilable C) | `owner:reloc_symbol` 32% of 121, `typed_index` 33%, `owner:pointer_table_deref` 20%, `single_use` 8% |
| Acts, rarely helps | `owner:argswap` (95% act, 1% clean), `owner:immediate` (0 of 24 improve) |

**No-ops depend on the compiler recipe, measured not assumed:** `register_storage` was a no-op on 72 of 72
game-recipe applications and acted on 9 of 9 libultra ones (IDO honours `register` only there);
`decl_order` is a no-op 84% under the game recipe and 35% under libultra.

## Four mechanisms that could not perform their action, and why

| Mechanism | Recorded failure | Root cause |
|---|---|---|
| `single_use` | `copyPackedMatrixTranslation`: `last` undefined | uses counted on comment-masked text, substitution applied to the raw text's first match, i.e. inside "Store the *last* column" |
| `typed_index` | `__osPfsRWInode`: "Unacceptable operand of '+'" | matched `(I*K) + T` in the middle of `ptr + (I*K) + off`, casting the integer `off` as the table |
| `owner:reloc_symbol` | `MusStartEffect`: array in a comparison; `insertHuffmanQueueNode`: redeclaration | an already-declared replacement keeps its own type (scalar -> array); a multi-line declaration was missed and cloned |
| `owner:pointer_table_deref` | `tryStartRacePlayerCourseObjectMode`: struct assigned to a pointer | removed `&` although the visible declaration is an array of structs |

Each fix has a regression test built from the recorded failing parent (`tests/test_machinery_repairs.py`,
fixtures `tests/fixtures/machinery_*`) that fails on the pre-fix code (checked against the frozen snapshot)
and passes now, plus a test that the mechanism still performs its action on the shape it exists for.

Replaying the fixed generators on every recorded application, no compiles (`replay_fixed.py`):

| Mechanism | Refused applications removed | Improving candidates lost | New candidates |
|---|---:|---:|---:|
| `single_use` | 52 / 52 | 0 / 48 | 5 |
| `typed_index` | 17 / 17 | 0 | 0 |
| `owner:reloc_symbol` | 35 / 39 | 0 / 16 | 2 |
| `owner:pointer_table_deref` | 2 / 2 | 0 (8 non-improving compiled ones removed) | 0 |

The first `reloc_symbol` guard required identical declared types and lost 3 of 16 improving candidates
(s8 -> u8, u16 -> u32 *, struct array -> struct array); the data set the rule instead: only array-ness must
agree. The 4 remaining refusals are symbols declared only in a project header, which the rewrite cannot see.

All 7 new candidates compile; 2 are exact for `dispatchRacePlayerMode07CourseObject` (independently
recompiled, recorded source-independent).

## The contrast: more search did not help

A pre-registered run gave the same `routed` search three times the budget (96 compiles) on all 216 functions
it had not solved (`../measured-potential-20260922/`): **0 new exacts in 216 functions, 18,346 compiles**, so
the pre-registered potential ranking could not even be scored (no positives in any quartile). The measured "potential" ordering (family
rates, residual conditioning, a one-step composition term) also added nothing beyond plain family rates
offline. The one gain of this session came from making a mechanism able to do its job.

## Not done

- `register_storage` should not run under the game recipe, and `commutative`/`decl_order` waste most of their
  compiles; the mutation stream does not know the recipe, so gating needs a caller-supplied recipe.
- Header-declared symbols remain invisible to `reloc_symbol`.
- The card should be joined to `solver/capability_contracts.py`: declared domain next to measured action.

Reproduce: `python -m eval.machinery_card ~/decomp/experiments/population-transfer-20260922/rows
~/decomp/experiments/population-transfer-20260922/stage2/rows --json card.json` (WSL).

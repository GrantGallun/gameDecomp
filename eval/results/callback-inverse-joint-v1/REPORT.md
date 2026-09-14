# Joint allocation/head/lifetime synthesis

The initial matrix compiled **91 candidates**, producing **36 distinct
interpreter inputs**. It combined eight allocation/index idioms, four head-base
forms, and three lifetime/store placements. Lower-scoring intermediate shapes
were included directly in pairs and triples. None improved the 97.454 seed.

After the structural agent discovered the 98.511 predecessor traversal, fifteen
allocation/store combinations were tested against that source. They produced
twelve distinct interpreter inputs. None exceeded 98.511; pointer-arithmetic
lookup tied it, lookup/base-first order scored 98.262, and index inlining scored
97.411. These results rule out these combinations, not the general strategy.

The final six-probe batch scoped the index declaration/assignment/lookup inside
a block (u16/u32/s32) or put a casted decrement inside the lookup expression.
All block scopes stayed at 98.511; cast-chain forms fell to 96.028 or 96.773.
See `../callback-inverse-joint-v3/scores.json`.

All 112 candidates compiled. Three generator tests passed. Since no new champion
was found, this branch did not submit a replacement or claim new semantic
validation. The structural agent owns validation of the 98.511 source.

The first matrix's delayed-allocation shapes were especially poor (roughly
80–88). Explicitly rebuilding the same head address frequently let IDO remove
the useful duplicate reload and lost about three score points. The successful
predecessor traversal restores the structural instruction sequence, leaving
register allocation as the principal remaining residual.

Every candidate and score remains in this directory or
`../callback-inverse-joint-v2`. `diversity.json` groups equivalent normalized
interpreter inputs without treating that grouping as a byte certificate.
The bounded experimental generator is `solver/callback_joint_search.py`; it is
not integrated into the production search because this experiment found no
productive new operator. Compiler settings and build guards were unchanged.

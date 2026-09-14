# Pointer-unit coverage and replay

The new shared repair handles typed parameter call addresses, including outer
casts that hide scaling, subtraction, commuted additions, address-of constant
indexing and simple pointer aliases. It requires a matching target call value;
an integer ratio alone does not authorize a rewrite or establish a type size.

Inventory: 1,355 pending functions, 46 proposals, 1,308 declines, one unavailable
ordinary-function parse (alEnvmixerPull). Numeric summaries use only the truncated
saved first_difference; this is not a complete classification of all instructions.
Reports retain each considered expression's change or decline reason.

Eight candidates were tested in v1; v2 excludes those and tests the remaining 38.
Across all 46, 45 compile and 44 pass frontend. Fourteen improve score with frontend
pass, including three fresh exact object certificates:

- func_800643B4: 99.583 -> 100
- updateMenuSpriteActorDebugControls: 99.923 -> 100
- initThrownTrailImpactProjectile: 99.667 -> 100

One candidate regresses (updateShieldProjectile); 31 scores are unchanged. The
pilot preserves original sources and makes no integration or campaign mutations.
Nonexact gains are not proofs of behavioral equivalence. The campaign's normal
semantic gates remain responsible for behavioral checks when enabled.

Validation: 211 main-tree tests, 107 staged-runtime tests. `runtime-smoke.json`
shows actual staged deterministic search obtaining the first exact match in one
repair compilation. Runtime staging and amendment scripts preserve the frozen
pipeline's other code, model settings and budgets. The four-file live amendment
has its own checkpoint, file hashes and rollback originals in the campaign's
`revisions/20260910-pointer-call-units` directory. Isolated pilot matches are not
manually added to campaign counts.

Remaining scope includes dynamic indices, complex aliasing and arbitrary stack
layout changes. Existing specialized generators continue handling their supported
patterns. No claim is made to recover all pointer expressions.

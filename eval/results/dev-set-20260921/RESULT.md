# One locally discovered repair, verified, and how far it transfers

Round of 2026-09-21, second half. This is the follow-up to `../../audit-fixes-20260921/REPORT.md`: that
round ended with the development panel as a fixed point of the catalog and no exact result. It ends here
with one function closed, the closing operator identified before it was run, and the same operator's
transfer to seven more functions measured separately.

Nothing was promoted. No model was called. The tier of the closed function is **`header-assisted`**, not
`SOLVED`, and that is stated in every receipt below.

## 1. The safety defect, fixed first

`opaque_variant` was the only action that could still fire on a finished candidate, and on this panel it
fired three times and destroyed all three. The cause was not the `void *` path it was written for but its
alias repair:

```python
# solver/compile_obligations.py, before
not re.search(r'\btypedef\b[^;]*\b' + re.escape(name) + r'\s*;', header_text)
```

`[^;]*` cannot cross a semicolon, so the guard could not see `typedef struct RacePlayer { ... } RacePlayer;`
— how every real struct is defined — and appended `typedef struct RacePlayer RacePlayer;` anyway:

```
cfe: Error: candidate.c, line 7: redeclaration of 'RacePlayer';
     previous declaration at line 243 in file 'include/game/race/player/race_player_input.h'
```

`typedecl.declared_in` already matched all three spellings and was written for exactly this failure; the
inline check re-derived a weaker version of it. The fix is to use the mechanism that exists
(`typedecl.typedefs`, which is also brace- and comment-aware), plus a guard that refuses to emit any
declaration the translation unit already has — decidable without a compiler, so an action can no longer
produce invalid C by construction.

| | from the policy candidate | from the frozen draft |
|---|---|---|
| destructive moves, before | **3** (all `opaque_variant`) | **3** (all `opaque_variant`) |
| destructive moves, after | **0** | **0** |
| compiles used, before / after | 3 / **0** | 39 / **36** |

Receipts: `search-policy.json` vs `search-policy-fixed.json`, `search-d2-b3.json` vs
`search-draft-fixed.json`. `destructive_moves` is now a first-class field of every search receipt: a move
that turns a compiling candidate into an uncompilable one, counted separately from the score, because the
score cannot see it (every caller keeps the better incumbent) and "nodes that do not compile" cannot see it
either (most nodes descend from a draft that never compiled).

## 2. A wiring defect that looked exactly like a negative result

The codegen reading (§4) said the residual is register allocation and pointed at `regalloc-search`, which
the registry advertises and which the nine-step intake catalog did not contain. The first
`--catalog registry` run reported **zero compiles over seventeen states** — which reads as "the registry has
nothing to offer here". It was not: the search resolved its runners from the intake dictionary alone, so
five of the ten registry actions were recorded `unavailable` on every state.

`search-registry-policy.json` is kept as `search-registry-policy.invalid-wiring.json` and named as invalid
rather than deleted or quietly re-run. The fix resolves every catalog entry through
`eval.tool_registry.ACTIONS`, and the three intake operators the probe had been driving without registering
(`source-type-declarations`, `undeclared-identifiers`, `or-address`) are now in the action space, so the
contract the collection, training and evaluation paths use is the complete one.

Two accounting defects came out of the same run:

* **`regalloc-search` compiles internally** (default beam budget 64). Counting one outer compile per action
  under-reported the work by up to 64× and let a search spend far past its declared allowance — the hidden
  internal compile budget the brief forbids. The runner's reported `compiles` is now charged, the action's
  own budget parameter is clamped to what remains, and one compile is held back so the result can always be
  certified.
* **"Stopped by the allowance" and "spent the allowance"** are now separate fields, because a search can
  reach its depth bound with every compile used, and reporting only the first makes two different findings
  look alike.

## 3. The result: one function closed

`__MusIntProcessWobble`, from the fixed policy's 97.045 candidate, in **one** action:

| | |
|---|---|
| action | `regalloc-search` |
| policy score | 97.045 (`compiled: true`, `exact: false`) |
| after the action | **100.0, `exact: true`** |
| compiles | 12 internal + 1 certifying, of a 40 allowance |
| solution sha256 | `6bbf6fb7994ea4c51d9fcc70954d83bb1de80c81246fdb63cc3930cd3b5b17d3` |
| assistance tier | **`header-assisted`** (the candidate's sequence used `header_variant`) |

**Independently verified** by `_verify_solution.py`, which re-derives the verdict from the recorded bytes
rather than trusting the search's claim:

```
solution sha256 (recorded)   6bbf6fb7994ea4c51d9fcc70954d83bb1de80c81246fdb63cc3930cd3b5b17d3
solution sha256 (recomputed) 6bbf6fb7994ea4c51d9fcc70954d83bb1de80c81246fdb63cc3930cd3b5b17d3
fresh verdict                compiled=True exact=True score=100.0
certificate                  {"exact": true, "kind": "mips_object_section_certificate",
                              "candidate_source_sha256": "6bbf6fb7..."}
exact attempts for this function existing BEFORE this candidate: 0
```

Zero exact attempts beforehand, so this is a new match and not a recount. It is **not** promoted into the
real build: that needs `requires_isolated_integration` and the ratchet's own acceptance test, and it is a
separate step.

The operator was named *before* it was run. The diff was read first (`_codegen_shape.py`), classified with
the project's own `solver.signals`, and it said: same instructions, different registers, delta 0. Then the
registry was searched and `regalloc-search` closed it. That ordering is the whole point — the search did not
find it by accident.

## 4. Transfer to separate functions

The same operator, applied independently to each of the seventeen states, from each state's own policy
candidate, with no state's result feeding another's:

| function | policy | with `regalloc-search` | delta |
|---|---|---|---|
| `__MusIntProcessWobble` | 97.045 | **100.000 exact** | +2.955 |
| `Fdistort` | 55.160 | 81.000 | **+25.840** |
| `FrandPan` | 67.368 | 88.158 | **+20.790** |
| `updateRacePlayerLeanAngle` | 39.403 | 43.956 | +4.553 |
| `osCreateViManager` | 74.222 | 78.515 | +4.293 |
| `updateRacePlayerMode06TerrainFall` | 90.451 | 91.067 | +0.616 |
| `updateFallingActionProjectileLanded` | 84.033 | 84.117 | +0.084 |
| `updateRacePlayerAirborneLaunch` | 94.822 | 94.869 | +0.047 |
| the other 9 states | — | unchanged | 0 |

**Closed: 1 of 17. Improved: 8 of 17.** Receipt: `search-registry-policy2.json` (depth 2, beam 3,
40 compiles/state, 455 compiles, 422 s, `destructive_moves: 8` — see §5).

That is a real transfer and a bounded one. The operator was not derived from any of these functions; it was
selected by reading one residual shape and it then moved seven other functions it had never seen, one of
them to exact. It did not move the remaining nine at all, and that is reported as the limit it is.

## 5. One more safety finding, of a different kind

The registry catalog's destructive moves are **8, all of them `redraft`** — which is *expected*: `redraft`
re-runs m2c without context and exists to throw the current candidate away. `opaque_variant` destroying a
candidate was a defect; `redraft` "destroying" one is its function. The counter cannot tell those apart, and
the report should not pretend it can: `destructive_moves` measures an effect, and the per-action breakdown
says which action and why.

## 6. What is left

1. **`Fdistort` (81.0) and `FrandPan` (88.2)** took the largest gains and were the natural next closes.
   **Measured, and they are not**: `search-deep-two.json` re-ran both with a 120-compile allowance, beam 2
   and depth 2 — 172 compiles, 96 s — and produced **no further gain at all** (`FrandPan` 88.158 with 119 of
   120 compiles spent, `Fdistort` 81.000). Both are local optima of this catalog, not states one step from
   done. The next thing they need is a residual reading, not a wider beam.
2. **Nine states did not move at all.** Their residuals need the same treatment this round gave
   `__MusIntProcessWobble`: read the diff, classify it, and only then look for an operator. `osEPiRawWriteIo`
   (91.474) has already been read and its shape is a register materialisation difference (`a3` reused against
   `t6`/`t8` plus an extra `addiu`), which is inside `regalloc-search`'s remit, but the search did not move
   it — worth understanding before widening anything.
3. **Promotion is untouched.** A header-assisted exact match still has to go through
   `requires_isolated_integration` and the ratchet.
4. **The intake catalog now looks under-powered rather than finished.** Nine declaration repairs, none of
   which owns the residual of a finished candidate. That is why the fixed order sat still on all seventeen.

## 7. Commands

```
# the safety fix
python eval/results/audit-fixes-20260921/reproduce_boundary.py          # exits 0
python -m pytest tests/test_compile_obligations_alias.py tests/test_typedecl.py tests/test_bounded_search.py -q

# the residual, read before anything was run
bash .cache/recon/bounded_search.sh                                     # both arms, WSL
python -m eval.results.dev-set-20260921._codegen_shape                  # the four highest scorers
python -m eval.results.dev-set-20260921._registry_declines              # why each action declines

# the result and its verification
python -m eval.bounded_search --dev-set eval/results/dev-set-20260921/dev-set.json \
  --out eval/results/dev-set-20260921/search-registry-policy2.json \
  --depth 2 --beam 3 --compiles 40 --start policy --catalog registry    # 455 compiles, 422 s
python -m eval.results.dev-set-20260921._verify_solution                # exits 0

# the two largest gains, given a much bigger allowance
python -m eval.bounded_search --dev-set eval/results/dev-set-20260921/dev-set-two.json \
  --out eval/results/dev-set-20260921/search-deep-two.json \
  --depth 2 --beam 2 --compiles 120 --start policy --catalog registry   # 172 compiles, 0 further gain
```

Full focused suite after all of the above: **237 passed, 2 skipped, 1 xfailed, 2 xpassed**
(`tests/`: rsi_coordinator, bounded_search, dev_set_export, end_to_end_transition,
compile_obligations_alias, typedecl, frontend_diagnostics, intake_frame, budget_ledger_enforcement,
generation_manifest_shapes, rsi_foundations, tool_boundary, tool_action_dataset, rsi_interventions,
m2c_or_address, source_type_declarations, m2c_placeholders, m2c_negative_offset).

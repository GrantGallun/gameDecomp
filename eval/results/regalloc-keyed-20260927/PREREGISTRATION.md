# Keyed resolution and site ranking in register search — preregistration

Written 2026-09-27, before any arm was run.

## What changed

`solver/regalloc_search.search` gained two options (default off; the campaign caller is unchanged):

- `key=` — before compiling a candidate, compute `ido_stages.optimizer_key`; if an earlier compile in
  this search had the same key, reuse that result instead of compiling. A reused candidate chosen to
  expand is compiled for real first. Ranking is unchanged, so the search path should be identical and
  only the cost per step changes. Each key is charged `key_cost = 0.14` compiles of budget.
- `rank_sites=True` — order each parent's variants: edits to lines owning a mismatched instruction,
  then edits to instruction-less lines, then the rest. An order, never a filter.

## Evidence it rests on (measured before this run)

- Same optimizer key ⇒ same object bytes: 174/174 campaign pairs across 8 strategy families, 293/293
  tree-pilot pairs (`refinement-data-20260927/analysis/noop_definition_check.out`, `optimizer_key_probe.out`).
- 37.1% of 77,916 logged register-search children compiled to their parent's object.
- Key 0.034 s vs full compile + score 0.243 s (tree pilot); the break-even no-op share is ~14%.
- Register-search edits, score-up rate by site class: owner 5.9%, instruction-less 4.8%, other 3.1%.

## Design

Cohort: 40 functions, seed 20260927, from 552 pending, register-dominant (the campaign's own gate),
unmatched in both ledgers (`cohort.json`). Three arms per function, each from its own baseline compile,
budget 200 compile-equivalents, `enable=True` as the campaign runs it: `plain`, `keyed`, `keyed+ranked`.
Every compile is logged; keyed resolutions are logged to `model_proposals`.

## Predictions

- **K1** Keyed resolution replaces at least 25% of candidate evaluations with a key match (keyed / all
  candidates evaluated, pooled).
- **K2** Zero key violations (a reused result whose real compile differs).
- **K3** The keyed arm's search path matches the plain arm's: in every function, the shorter arm's
  sequence of evaluated labels is a prefix of the longer one's.
- **K4** At equal budget, `keyed` matches at least as many functions as `plain`, and every function
  `plain` matches, `keyed` also matches.
- **K5** The measured key/compile time ratio is at most 0.2 (the budget charge assumes 0.14).
- **R1** (secondary, weak prior) `keyed+ranked` matches at least as many functions as `keyed`. The
  ranking's measured edge in register search is small (5.9% vs 3.1%); a null here is expected and is
  not a reason to tune the ranking.

A K2 or K3 failure means the equivalence argument is wrong and keyed resolution does not ship, whatever
K4 says.

# Rule miner: finding IDO spelling rules by program, not by hand

PRE-REGISTRATION, written 2026-09-29 before any mining run.

## Why

Today four IDO rules were found by hand (unaligned copy, -O1 copy-back temporaries, array element vs named field,
rotated loop vs `for`). Each was real but covers about 2% of unsolved functions. The held-out 50 test
(`../heldout50-20260929/`) was not significant (7:2, p = 0.090), and one shape edit fired in 50 functions. Coverage is
the gap, and finding rules one at a time by reading doesn't scale.

## Design (the part of each paper that fits here)

- **Ruler / Souper**: enumerate candidate rewrites, let the oracle validate them, prune redundant ones. Here that is
  *Engine A*. A library of generic, semantics-preserving, two-way C rewrites (`solver/rewrite_library.py`) is applied
  to functions we already match exactly (our own oracle-verified sources, never recovered or reference-copied ones).
  Each variant is compiled. The compiler labels each (rewrite, direction) with an effect rate and the residual
  features it creates. Inert rewrites (no listing change) are pruned, which is Ruler's minimisation applied to IDO.
- **Getafix / Revisar**: learn edit templates from real before/after pairs by anti-unification. Here that is
  *Engine B*. It reuses `patterns/equivalences.edit_of` (identifiers and constants abstracted consistently)
  over logged campaign parent→child edges and records which residual features each abstracted edit *removed*. It
  adds a placeholder matcher so a template can be applied to new source. No compiles.
- **SAILR**: invert the compiler's transformations rather than generic cleanup. The features are IDO-specific
  (residual classes, abstracted instruction deltas), so a rule means "this spelling causes this IDO shape".
- **BED / permuter**: the compiler stays the only judge. Mined rules only order and supply candidates for the
  existing site-edit search.

Matcher: for an unsolved candidate, each applicable rule is scored by how well its feature profile matches the
candidate's residual features (idf-weighted). Top-scoring variants form a `mined` lane after the shape lane.

## Tests and predictions

- **T1 rediscovery (Engine A)**: without being told, mining finds that rotated-loop↔`for` and copy-back
  temporary↔direct assignment change IDO -O1 output, and each ranks top-3 among applicable rules on the
  osMotorStart chain stage where it was the right move (`../temp-copyback-20260929/`). Prediction: both succeed.
  Unaligned copy and array/field are specialised, not in the generic library, and not expected.
- **T2 pruning**: report each generic rewrite's effect rate. Prediction: increment spellings (`x++`/`x += 1`/
  `x = x + 1`) and comparison flips are inert (<5%). Loop and temporary rewrites are not.
- **T3 Engine B yield**: number of abstracted edit templates with ≥ 5 listing-changing edges across ≥ 3 functions,
  and their feature profiles. Prediction: tens to low hundreds.
- **T4 development pool**: 50 functions drawn from the 418-function development pool (not the sealed 50), the site-edit search with
  the mined lane against without it (current defaults). One-sided sign test on best gradient. Prediction: mined
  lane fires (≥ 1 mined edit compiled) on ≥ 60% of functions and wins more than it loses.
- **T5 sealed held-out**: only if T4 is positive. Re-run the sealed 50 with the same control as before. The decision
  rule from `../heldout50-20260929/PREREGISTRATION.md` applies unchanged.

## Results so far, and the T5 amendment (written before T5 runs)

- T1: PASS on all four osMotorStart/osMotorStop stages (`t1_rediscovery.py`): loop:rotated->for ranked 3 of 7, temp
  copy-back 1 of 6.
- T2: 21 rule directions, 14 pruned (inert or too rare). Inert as predicted: increment spellings (pe->pp 1.9%;
  pp->pe 11% on n=18, above the 5% bar), truth tests, ternary, subscript/pointer. Two of my rewrites had precedence bugs
  (fake 55% "effect" for truth tests, 32% broken compiles for subscripts); fixed with tests, re-mined.
- T3: 360 Engine B templates kept, 346 usable, 253 applicable to new code, 41 with exact children (prediction held).
- T4: mined lane fired in 46/50 (92%). Wins/losses/ties 13/2/35, one-sided p = 0.0037. 11 of 13 winning best states
  come from a mined edit. 0 exact in both arms. Compiles 1,720 vs 1,323.

T5, sealed held-out 50 (`../heldout50-20260929/frame.json`): treatment = current defaults with `mined=True`
(`t5/run_treatment.py`). Control = the recorded control arm of `../heldout50-20260929/` (this morning's search,
unchanged code path, deterministic compiles), reused, not re-run. Decision rule unchanged: wins > losses and one-sided
sign-test p < 0.05 → mature, move on. Prediction: fire ≥ 80%, significant.

## Upgrade: B→A promotion and residual localisation (written before promotion results or any upgrade run)

T5 result: 17/3/30, p = 0.0013, 0 exact. The user asked to move the mechanism toward maturity on the mechanism map.
Two upgrades, from the ceiling analysis:
- **Promotion** (`eval/rule_mine.py p`, the Ruler step Engine B lacked): each promotable B template (reverse
  instantiable, precision ≥ 2%) is applied *in reverse* to exact corpus functions (≤ 10 per function) and compiled.
  Inert on correct code (≥ 3 compiled, effect < 5%) → pruned. ≥ 3 changed → the controlled profile replaces the search
  history profile (`validated`).
- **Localisation** (`rule_miner._localised`): score × (1 + share of residual line weight the edit touches), using
  `site_edits.site_lines`. Engine A generates up to 12 sites per rewrite when sites are known.

Tests:
- **U1 promotion yield**: prediction ≥ 50 templates validated and ≥ 20% of probed templates pruned as inert.
- **U2 development frame** (the T4 frame): upgraded search against the recorded T4 mined arm (same code except the
  upgrades; deterministic compiles). Prediction: wins > losses.
- **U3 sealed 50**: upgraded search against the recorded T5 treatment. One-sided sign test. Prediction: wins > losses.
  Significance is not predicted; both arms already carry the mined lane. Reported with exacts and compiles either way.

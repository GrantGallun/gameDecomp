# Rewrite generation (Engine G): pre-registration

Written 2026-09-30, after generation and probing and while validation ran. Nothing below the "Tests" line had been
measured when it was written.

## Why

The rule miner (`../rule-miner-20260929/`) passed T5 on the sealed 50 (17/3/30, p = 0.0013) with 0 exacts, and both
upgrades (promotion, localisation) were neutral. The reading was that ranking is not the constraint, vocabulary is:
Engine A has 10 hand-written rewrite families, and Engine B only knows edits the search already made. Engine G
generates new vocabulary automatically.

## Mechanism

`eval/rewrite_enum.py` plus `solver/term_rewrite.py`. The recipe, from the literature:

1. **Shapes (Enumo: workload-guided).** Every expression subtree of our own exact sources (1,053 functions, no
   recovered or reference-copied attempt), cut into patterns of at most 3 operators and 3 placeholders. A shape used
   in at least 3 functions seeds a class. Observed: 465 shapes, 172 seeds.
2. **Siblings (Ruler: enumerate and fingerprint).** All terms of at most 2 operators over E0 to E2, N0 and 8
   constants (513,162 terms), fingerprinted by C semantics under signed and unsigned operands. Undefined inputs must
   agree, and so must the result type. A term with a seed's fingerprint is a sibling. Padding is pruned (identity
   operations, split constants, reused operands), and at most 12 siblings are kept per seed. Observed: 623 pairs.
3. **Probes (Souper: label with the real compiler).** Each term is compiled by the game's IDO recipe in 4 contexts
   (return, branch condition, narrow parameters, struct fields). A pair that compiles identically in all 4 is inert
   and dropped. Observed: 374 pairs kept, 249 inert, 0 broken.
4. **Validation (Engine A's method).** Each direction is applied once to exact functions (at most 12 directions per
   function, least-covered first) and compiled. The residual features become the direction's profile. A direction
   with fewer than 3 changes, or changing the listing under 5% of the time, is pruned.
5. **Use.** `rule_miner._generated`: a usable entry `r => l` whose profile scores against a candidate's residual
   offers `l -> r` wherever the candidate spells `l`, applied on the parse tree with precedence-safe grouping. It
   competes with Engines A and B for the mined lane's 8 slots.

Known limit, decided before the tests: a rule must hold for both signed and unsigned operands, so signed-only
identities such as `x << 16 >> 16` versus `(s16)x` are excluded. Operand types are not known at the match site.

## Tests

- **G2 validation yield.** Prediction: at least 50 usable directions, and at least 20% of validated directions pruned
  as inert in context (probe-distinct in isolation does not imply distinct inside a real function).
- **G3 development frame** (`../rule-miner-20260929/t4/frame.json`). The current search with the rebuilt table
  (A + B + G) against the recorded U2 arm (`../rule-miner-20260929/upgrade/u2.jsonl`), paired by best
  `site_edits.gradient`. Prediction: wins > losses, and a G edit is tried in at least 50% of functions.
- **G4 sealed 50** (`../heldout50-20260929/frame.json`), run only if G3 has wins > losses. The same arm against the
  recorded U3 arm. One-sided sign test. A positive result at p < 0.05 means the upgrade is adopted. Exacts, mean best
  score and compiles are reported either way.

Both comparison arms are recorded runs of the code before Engine G. The table rebuild also shifts idf weights for
Engines A and B, which is part of the treatment. The sealed 50 is not used for any choice before G4.

## Amendment G3b (written after G3, before running G3b)

G3 failed as registered: 1 win, 1 loss, 48 ties against U2 (p = 0.75), and a G edit was tried in 4 of 50 functions
against a predicted 50%. G2 passed (157 usable directions; 125 of 300 directions with at least 3 compiles pruned as
inert, 42%). Therefore G4 is **not** run for the G3 arm.

Diagnosis (`diagnose_g3.py`, `diagnose_g3.json`): a G entry scored against the residual in 47 of 50 functions, and
one both scored and applied in 37. But the best G proposal ranked in the mined lane's top 8 in only 3. Engine B's 344
templates outrank it. The failure is lane allocation, not generation.

Change: `rule_miner.GENERATED_SLOTS = 2`. Two of the lane's 8 proposals are kept for Engine G when it has any. This
was chosen on the development frame, which is its purpose.

- **G3b development frame.** Same frame, same comparison (recorded U2), fresh trial database. Prediction: G edits are
  tried in at least 50% of functions, and wins > losses.
- **G4b sealed 50**, only if G3b has wins > losses. The G3b code against the recorded U3 arm. The G4 rule is
  unchanged: positive at p < 0.05 means adopt.

## Amendment G3c (written after G3b, before running G3c)

G3b came out the same as G3: 1/1/48, p = 0.75, identical compiles (1,824). G edits were tried in 5 of 50 functions.
Tracing the lane (`trace_lane.py`) found a bug, not a ranking effect. `rule_miner._generated` capped the **scored** G
entries at 24 before checking whether they applied. On most functions none of the top 24 matched, so G proposed
nothing and the reserved slots stayed empty. The G3 diagnosis had counted applicability over all entries, which is
why it pointed at ranking.

Fix: the cap counts rules that apply. A regression test was added: 40 high-scoring rules that do not apply must not
hide one that does. On 12 dev functions where G applies, G now reaches the lane in 12. G3 and G3b are void as tests of
Engine G, because it barely ran in either.

- **G3c development frame.** The same design as G3b with the fix (fresh trial database). The predictions are unchanged:
  G tried in at least 50% of functions, and wins > losses.
- **G4c sealed 50**, only if G3c has wins > losses. The same rule as before.

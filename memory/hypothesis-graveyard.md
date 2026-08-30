# Hypothesis Graveyard

Tested hypotheses and their evidence. "Graveyard" means the claim is no longer floating untested; it may be confirmed, refuted, inconclusive, or superseded.

Update rules:
- Move tested hypotheses here whether they succeed or fail.
- Include the test, evidence, decision, and linked idea when available.
- Prefer superseding or correcting old entries over leaving contradictory claims unresolved.

Created: 2026-08-29

## Tested Hypotheses

### HYP-20260829-01: An asm-differ similarity score of 100 is sufficient evidence that an attempt is byte-exact.
- Status: Refuted
- Tested: 2026-08-29
- Test: Audit every attempt writer, match consumer, router, and near-miss selector; add explicit verifier receipts and adversarial score/verdict tests.
- Evidence: The oracle exposes exact separately; a 100-score relocation mismatch is possible. Before this change the attempts table discarded exact, matched/status inferred it from score, and six selectors excluded score-100 nonmatches. All 218 tests pass after fail-closed migration where historical rows remain NULL.
- Decision: Persist the verifier exact boolean on every new attempt; treat legacy rows as unknown; use score only for ranking and route score-100 nonmatches to relocation.
- Linked ideas: None

### HYP-20260829-02: An unchanged diff-repair run is evidence that the repair method failed.
- Status: Refuted
- Tested: 2026-08-29
- Test: Add activation and compilation receipts, then replay three stored near misses against a disposable KB copy.
- Evidence: The arm activated and compiled on 2/3 functions, improving 99.436 to 99.925 and 95.989 to 96.211. On bootThreadMain it generated zero constraints, so the repair was not applicable and no effectiveness comparison occurred. 221 tests pass.
- Decision: Report not_applicable separately from no_gain, regression, and build failure; invalidate a corpus experiment if the arm never activates.
- Linked ideas: IDEA-20260829-02, IDEA-20260829-03

### HYP-20260829-03: Choosing a typed-residual Pareto-top candidate instead of scalar-best improves deterministic repair closure.
- Status: Inconclusive
- Tested: 2026-08-29
- Test: After append-only reverification removed 34 historical exact functions, rescore stored candidates for 13 unresolved functions above 90 and repair the two functions where Pareto-top differs from scalar-best.
- Evidence: Pareto selected a different anchor on 2/13 functions. Neither arm produced an exact match. On getRacePlayerRankingProgress Pareto reached 96.019 versus scalar 95.330; on initRacePlayerLandingSnowSpray neither improved beyond its anchor. Applicable pass counts differed (3 versus 2 or 1), so this is signal but not clean causal proof.
- Decision: Keep Pareto selection in the evaluator only. Do not integrate into the production pipeline until it produces closures or wins under a budget-matched larger replay.
- Linked ideas: IDEA-20260829-03

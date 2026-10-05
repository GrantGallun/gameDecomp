# load_modify_stores linear-gap hotfix (2026-09-14)

**Defect.**
- The deployed `regalloc_search` generator `load_modify_stores` matched the statements between a load and its update
  with the regex `(?:[ \t]*[^;\n]*;[ \t]*\n?)*?`.
- The quantifiers overlap: `[ \t]*` against `[^;\n]*`, and an optional newline. When no update follows, the regex
  backtracks exponentially.
- Offline, dominant-1 spun for 4.5 CPU hours on initMainMenuSceneModelParts and guPerspectiveF and made no progress
  (`eval/results/regalloc-20260913/hang_repro.py`).

**Live exposure.** `eval/results/regalloc-20260913/hang_exposure.py` → `hang-exposure.json`:
- 17 of 454 queued `regalloc_search` nodes spin for more than 5 s on the live generator.
- The fixed generator spins on none, with identical outputs on the other 437.
- Campaign workers have no per-job timeout, so a handful of such dispatches would hold every worker slot.

**Fix.** `_statement_ends` computes the same gap offsets with a linear scan, and the update pattern is matched at each
offset, shortest gap first. The main-tree module differs from live only by this change.
- Test: `test_load_modify_store_skips_intervening_statements_and_stays_linear` covers both halves:
  - the generator fires across 40 intervening statements;
  - the no-update case finishes in under 1 s.

**Protocol.** `stage.py`; staged full suite `staged-tests.log` (2617 passed); pause and drain; `deploy.py`
(revision `20260914-regalloc-linear-gap`); resume.

**Authorization.** This fixes a defect in the user-approved regalloc_search amendment (2026-09-13). It adds no new
capability and changes no profile, gate, schedule or budget.

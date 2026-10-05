# Frozen type/layout repair round — September 22, 2026

Same 200 header-assisted candidates as `clean-calls-20260922/final.json`.
These are isolated experiment counts, not production SOLVED totals or recovered-type claims.

| Measurement | Before | After |
|---|---:|---:|
| IDO compiled | 60 | 79 |
| Frontend passed | 58 | 77 |
| Both passed | 57 | 76 |
| Object exact | 7 | 8 |
| Frontend diagnostics | 1853 | 1754 |

64 final sources improved; 50 have fewer frontend errors.
No prior compiler pass, frontend pass, exact object, complete error count or compiled similarity regressed.

![Overlapping frontend and object histograms](histogram.png)

## What changed

- Binary-observed scalar loads can repair a header-declared aggregate global used as a bare binary operand. Operator position, operand side, reported type, declaration and unanimous direct zero-offset loads must agree. No field name or struct layout is invented.
- Header-locked function definitions preserve candidate body views through typed local aliases for supported equal-width o32 slots. These are header-assisted ABI hypotheses.
- Fresh complete diagnostics feed both owners through the existing bounded intake sequence. Every child is compiled and logged; the incumbent ratchet decides adoption.
- The separate object pass exercises existing rewrite rules, plus existing compound-assignment, commutative and declaration-order generators on >=99% residuals containing only register/order differences. No new register mutation is implemented here.

Review regressions cover unrelated `sizeof`/member uses on the same diagnostic line, parenthesized and macro-defined local shadows, operand-side/type binding, and binary `&` versus address-of.

## Current blockers

| Frontend class | Functions before | Functions after | Diagnostics after |
|---|---:|---:|---:|
| member-on-scalar-or-array | 42 | 44 | 349 |
| member-on-void | 43 | 43 | 403 |
| undeclared-member | 37 | 37 | 255 |
| non-pointer-subscript | 31 | 31 | 268 |
| undeclared-identifier | 22 | 22 | 98 |
| other-syntax | 21 | 21 | 59 |
| call-arity | 17 | 17 | 23 |
| non-pointer-arrow | 15 | 15 | 137 |
| redeclaration/conflict | 44 | 15 | 16 |
| incompatible-aggregate | 11 | 11 | 11 |
| invalid-binary-operands | 34 | 9 | 43 |
| array-assignment | 7 | 7 | 31 |
| not-callable | 6 | 6 | 6 |
| incomplete-array-element | 5 | 5 | 5 |
| unclassified | 4 | 4 | 5 |
| incompatible-int-pointer | 4 | 4 | 27 |
| undeclared-function | 3 | 3 | 3 |
| missing-prototype | 3 | 3 | 4 |
| aggregate-where-scalar-required | 2 | 2 | 2 |
| incompatible-pointer | 2 | 2 | 8 |
| unknown-type-name | 1 | 1 | 1 |

Categories overlap: 99 distinct functions have member/access-representation blockers;
103 have either those or declaration conflicts.
Diagnostic volume is not a repair distance. A function can require several repairs, and a single shared representation mistake can cause many diagnostics.

Current disjoint stages: `{"compilation-blocked": 124, "object-exact": 8, "object-mismatch": 68}`.
More compiled nonmatches means more functions reached object comparison; it does not imply a regression.

Sole remaining frontend classes: `{"array-assignment": 2, "call-arity": 2, "incomplete-array-element": 3, "invalid-binary-operands": 2, "member-on-scalar-or-array": 7, "member-on-void": 12, "missing-prototype": 1, "non-pointer-arrow": 1, "non-pointer-subscript": 3, "not-callable": 2, "other-syntax": 1, "undeclared-function": 2, "undeclared-identifier": 1, "undeclared-member": 1}`.
Concrete sites, prerequisites and close object residuals: [next-frontier.json](next-frontier.json).

## Exact results

- `FrandNote`: independently compiled and object-certified; 81 previous exact attempts in the read-only research DB.

The added `FrandNote` result is a recovered prior research match, not a new global discovery. The clean intake route now reproduces it from this cohort's starting state.

Exactness is the workspace's object certificate, including compared sections and relocations under the recorded compiler environment. It is not a whole-ROM integration certificate.

## Measurement and validation

- Fresh baseline: 200 native WSL builds, reproducing all seven prior exact objects.
- Final intake: 200 functions, 591 logged children, three rounds / twelve children / beam width three per function. Stops: `{"attempt-budget": 12, "exact": 7, "fixed-point": 180, "round-budget": 1}`. A budget stop is not convergence.
- Object pass: 69 compiled nonmatches examined, 234 compiled children; at most twelve shared rewrites plus twelve register/order hypotheses per qualifying state.
- Final focused validation: 129 intake/type tests and 3 existing object-generator tests passed against the frozen implementation.
- Full repository suite: `13 failed, 4421 passed, 7 skipped, 1 xfailed, 2 xpassed, 41 errors in 196.37s (0:03:16)`. All 54 failing test identities match the previous round. This full run preceded the last two narrow guard changes; the final focused suite covers them.
- Audited 1147 private attempts, including interrupted pilots, and 791 final intake nodes. Source hashes, parent edges and final verdict bindings passed.
- Read-only binary evidence: 72845 rows, SHA-256 `ca44b535f0e10bccba2f84d99c02efa1fcfe021b5dacb106f36b3be5da7bde97`.
- Four independent native workers used one frozen implementation snapshot: [frozen-code.json](frozen-code.json). Unrelated concurrent workspace edits caused a strict resume check to reject a serial pilot; final results use `paired-frozen.json`. `paired-confirmed.json`, `paired-validated.json`, and `proposal-census.json` are exploratory artifacts, not final measurements.

Authoritative artifacts: [final.json](final.json), [summary.json](summary.json), [accepted-lineages.json](accepted-lineages.json), [attempt-log-check.json](attempt-log-check.json), [code-verification.json](code-verification.json), [exact-confirmation.json](exact-confirmation.json).

Candidate sources remain under `states-frozen/`. Attempts and native build workspaces are under `/home/grant/decomp/experiments/clean-types-20260922/`. This round does not mutate production KB inference, integrate game C, or deploy the frozen campaign. Held-out reference function bodies were not inputs to these repairs.

# Frozen storage repair and routing loop — September 22, 2026

Same 200 header-assisted candidates as `clean-types-20260922/final.json`.
These are isolated cohort counts, not production SOLVED totals or recovered-layout claims.

| Measurement | Before | After |
|---|---:|---:|
| IDO compiled | 79 | 98 |
| Frontend passed | 77 | 96 |
| Both passed | 76 | 95 |
| Object exact | 8 | 8 |
| Frontend diagnostics | 1754 | 1207 |

47 retained sources improved; 38 have fewer frontend errors.
No incumbent exactness, IDO pass, frontend pass, complete error count or compiled similarity regressed.

![Overlapping frontend and object failure histograms](histogram.png)

## Reusable machinery and the routing gap

- `solver/global_field_view.py` proposes byte lvalues for diagnosed named globals.
  Included declaration, source site, target symbol/offset, direction and width
  must agree. It does not invent field names or complete record layouts.
- `solver/stack_scalar_arrays.py` proposes contiguous indexed word storage with
  witnessed stack slots and merges supported overlapping scalar aliases.
  Stack-name correspondence is a hypothesis, still subject to compilation and
  object comparison. Ambiguous syntax, shadows and access widths are declined.
- Both owners run in the normal bounded intake sequence and in
  `solver/modelrepair.search(resilient=True)`. The campaign path also now tries
  prior global-scalar, header-signature, call-arity and frontend-cast owners that
  had only been connected to intake. Existing void/ABI owners get refreshed
  complete diagnostics. Children use ordinary `workspace.score` and attempt logging.
- The real native compiler pilot invokes `modelrepair.search` with zero model
  calls and takes a previously blocked function through IDO and frontend checks.
  Fixture entry-point tests verify actual proposal generation, scoring, parent
  receipts and retention. See [pilot.json](pilot.json) and
  `tests/test_modelrepair_frontend_routes.py`.
- No function-name branches or expected-output substitutions were added to the
  production generators. The compiler/object certificate adjudicates candidates.

This closes a real implementation gap: earlier measured intake gains did not all
reach campaign normalization. The separate scripted tool-agent action catalog is
not expanded here. Existing frozen campaign runtimes are not automatically
updated by working-tree changes; this loop does not claim a deployment.

## Current blockers

| Frontend class | Functions before | Functions after | Diagnostics after |
|---|---:|---:|---:|
| member-on-void | 43 | 43 | 403 |
| member-on-scalar-or-array | 44 | 41 | 208 |
| undeclared-identifier | 22 | 22 | 98 |
| other-syntax | 21 | 21 | 59 |
| call-arity | 17 | 17 | 24 |
| undeclared-member | 37 | 16 | 40 |
| non-pointer-arrow | 15 | 15 | 137 |
| redeclaration/conflict | 15 | 15 | 16 |
| non-pointer-subscript | 31 | 12 | 76 |
| incompatible-aggregate | 11 | 11 | 11 |
| invalid-binary-operands | 9 | 9 | 43 |
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

Categories overlap. 81 distinct functions still
have member/access representation blockers. Diagnostics are observations, not
independent causes or a distance to a match. Multiple errors can share one cause;
removing one class can reveal another.

Disjoint stages: `{"compilation-blocked": 105, "object-exact": 8, "object-mismatch": 87}`.
Compiled nonmatches appear only after compiler admission; growth in that category
can accompany progress. Object axes overlap as well.

Sole remaining frontend classes: `{"array-assignment": 2, "call-arity": 2, "incomplete-array-element": 3, "invalid-binary-operands": 2, "member-on-scalar-or-array": 5, "member-on-void": 12, "missing-prototype": 1, "non-pointer-arrow": 1, "non-pointer-subscript": 1, "not-callable": 2, "other-syntax": 1, "undeclared-function": 2, "undeclared-identifier": 1, "undeclared-member": 2}`.
Concrete source sites, conditional owner routes and close object residuals:
[next-frontier.json](next-frontier.json).

## Exact results

No additional exact objects in this loop.

An object certificate covers compared sections/relocations under the recorded
compiler environment. It is not a whole-ROM integration certificate. Prior
research matches are labeled recovered, not new global discoveries.

## Verification and receipts

- Fresh 200-function native WSL baseline reproduced all 8 prior exact objects.
- Frozen intake replay: 572 logged children, three rounds /
  twelve children / beam width three per function. Stops:
  `{"attempt-budget": 10, "exact": 8, "fixed-point": 180, "round-budget": 2}`. Budget stops are not convergence.
- Object pass: 87 compiled nonmatches examined,
  239 children from the existing shared rewrite catalog and
  bounded existing register/order generators. No new backend mutation here.
- Focused suite against the frozen implementation: `212 passed in 31.07s`.
- Full suite: `15 failed, 4506 passed, 8 skipped, 1 xfailed, 2 xpassed, 41 errors in 285.82s (0:04:45)`. 2 additional failing identities reproduce against the prior frozen implementation: Windows process-control tests cannot execute wsl.exe inside this WSL environment. The other failing identities are unchanged.
  56 failure reports remain. See [tests-comparison.json](tests-comparison.json)
  and [prior-code reproduction](tests-prior-recheck.json). The final additional
  compiler-rejection guard test is included in the focused run.
- 1030 private attempts, including pilots and
  failed candidates; 772 search nodes and
  239 object children audited. Source hashes, parent
  edges and final source/verdict bindings passed.
- Binary evidence: 72845 immutable copied rows, SHA-256
  `ca44b535f0e10bccba2f84d99c02efa1fcfe021b5dacb106f36b3be5da7bde97`. No production KB inference mutation.
- Four disjoint workers used the same [frozen code snapshot](frozen-code.json).
  Measured module hashes match the working tree. Candidate files remain under
  `states-frozen/`; native workspaces and attempt DB are under
  `/home/grant/decomp/experiments/clean-fields-20260922/`.

Authoritative artifacts: [final.json](final.json), [summary.json](summary.json),
[accepted-lineages.json](accepted-lineages.json), [attempt audit](attempt-log-check.json),
[code audit](code-verification.json), [exact confirmation](exact-confirmation.json).
No held-out game function bodies were supplied to proposals, prompts or the KB.

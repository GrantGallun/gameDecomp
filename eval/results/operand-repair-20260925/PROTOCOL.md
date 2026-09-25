# Protocol: operand repair with full evidence on the 130 structurally-correct campaign functions

Written 2026-09-25 before `operand_repair.py` ran. Motivation: `alloc-census-20260924` — 50 of 108 diagnosed functions
have uopt colouring identical to the target, with residuals in operands (shape, offset, immediate, symbol); the
classifier marks 80 of their 136 operand steps "stated". A wiring gap explains part of why they were never closed in
the binary-types searches: `evidence_site` (the stated-fix owner) needs the verdict's `source_attribution`, which the
copied-workspace harness never computed, and `diffrepair` was not in the stream.

## Method
Pool: the 130 (all of them, census class recorded; the `none` group is the primary target). Start: each function's
campaign best source. Scoring: `solver.workspace.score` (main tree) in an isolated repo per function
(`eval.campaign_workers.isolate`), logging into a PRIVATE database (KB schema; functions/tus copied), so every verdict
carries diff, frontend and source_attribution. Proposals per step: `solver.regalloc_mutations.variants(source, name,
diff, evidence=verdict)` (evidence_site, frontend_type, owners, branch_shape, ...) plus `solver.diffrepair.repair`.
Greedy: 8 children per step, best improving child (exact, score) becomes the parent; 40 compiles, then two restarts of
16 from the best node. Reproduction: the baseline must reproduce the campaign's recorded score within 0.01, else the
function is reported apart.

## Recording
Exact candidates (certificate plus frontend gate: `Attempt.exact` and a passing frontend) are rescored through
`workspace.score` with the production KB connection, strategy `operand-repair-20260925:<tier>`. Tier:
`project-header-assisted` if the source includes `game/` headers, else `source-independent` (eval.status also
applies its reference-type test). Each recorded function is checked against the campaign ledger (all 130 are
campaign-unmatched by construction, so every exact is new to the project).

## Reported
Exact / improved / flat by census class; the families on exact paths; how often evidence_site and diffrepair fired
(a family that never fires on this pool is a finding, the silent-decline rule).

## Amendment A1 (2026-09-25, after the 130-function run): the next band
Same method, recording and reporting on the frontier functions with 1-2 structural steps (small and medium,
`--band 1-2`; 129 functions). Summary in `summary-band1-2.json`; recorded with `record.py` (rows share `E/rows`, and
already-recorded functions are skipped by their exact status).

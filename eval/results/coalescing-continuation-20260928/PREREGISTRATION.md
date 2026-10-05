# Continuation from the coalescing sweep

Authorized by the user to continue the improved candidates, compare original
roots and keep a bounded same-object alternative set, with generous budgets.
Use the existing register-search and strict native research-suite oracle.

Choose all non-exact functions with a retained compiling child improving either
the full-listing gradient or the retained weighted score. Keep the original,
best gradient child, and best score child when distinct. Additionally retain at
most one certified same-object child per function for the four closest eligible
functions when it offers new successor source strings in a 64-candidate prefix.
This measures a bounded source neighborhood, not behavioral novelty or necessity.
Tie-breaks are deterministic. Do not use reference bodies or known exact winners.

Freeze source/object/header context and compiler/implementation hashes before
new compiles. Initial allowance is 512 effective units per starting source,
including root, failures, probes, audits and expansion checks; key calls cost
0.14 under the existing model. Use evolvability_coalesce, enabling roots, 2%
audit, seed 0, beam 4, depth 12, preview 64, probes 2, exploration 0.2. Four
workers use the idle four-core WSL environment. No live campaign or ledger writes.

Compare each retained-root run with its original-root run at the same budget.
Extra score/no-op roots spend extra work and are exploratory; do not aggregate
their wins into an equal-total-budget continuation advantage. Root selection
uses prior exposed observations, so this is an adaptive development experiment,
not a fresh held-out evaluation. Assistance is unknown pending a lineage audit;
the build uses project headers, and nothing counts as clean/unassisted training.

Primary: source-bound byte certificate plus passing frontend. Secondary: best
actual-compile full-listing gradient. Check fresh baselines against retained
gradients. Log all sources, source parents, objects, checks and costs. Preserve
terminal reason; the existing engine's `exhausted` also covers finite depth or
beam pruning and does not prove the entire mutation graph is exhausted.

Inspect initial results and extend promising sources in separately recorded
rounds with larger budgets or fresh seeds. Include matched-budget controls for
any claimed comparison. A literally infinite run is not required: stop a branch
when the configured frontier is exhausted or repeated rounds offer no new
progress, while preserving its source and logs for later vocabulary additions.

## Adaptive extension selected during the first round

Before running the separate model-guided probes, select
`updateRacePlayerTrickSubstateHold`: its original-root continuation reached
`[2,20,22]`, while its same-object alternative reached `[0,18,20]`. Freeze each
run's own actually compiled best source into a new bundle. Give each 2,048
additional effective units with all other search settings unchanged. This is a
restart from the retained best source, not a resumption of its old frontier.
Account for the preceding 512-unit run separately. Selection is adaptive; this
pair measures what another round can achieve on this case, not average yield.

Separately test 12 model-guided hypotheses across three functions plus their
three baseline compiles (15 calls): pause-menu palette local reuse and its final
comparison constant, aerial-trick timer live-range splitting, and hold-state raw
timer versus named-mask representation. Inputs are retained candidates and
target assembly only. Keep these results separate from automatic-search results.

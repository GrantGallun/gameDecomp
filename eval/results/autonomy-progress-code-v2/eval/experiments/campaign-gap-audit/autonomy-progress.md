# Autonomy progress experiment (September 6, 2026)

Preselected eight functions with no attempts at cutoff 30173, two per game/SDK
and medium/large stratum. Seed, exclusions and budgets are in
`eval/results/autonomy-progress-8-v1.cohort.json`. Header-assisted; no reference
function bodies or historical candidate seeds. No integration.

## Frozen v1: operational failure, not an autonomous success

Snapshot: `eval/results/autonomy-progress-code-v1`.
Checkpoint: `eval/results/autonomy-progress-8-v1.json` (preserved unchanged).
Eight intake visits completed: two compiler successes (one frontend pass), five
compiler failures, one privileged-instruction backend blocker. No model calls
had occurred. The next recovery visit crashed in `workspace.score`: explicit
`extra=None` could not be expanded as a mapping when attaching frontend evidence.

Fix: treat absent/None logging metadata as an empty mapping, retaining frontend
and compiler recipe evidence. Six regression cases cover None/empty/populated
metadata with and without frontend results. This is an engineering intervention.
The earlier lifecycle fix defers semantic panels until a frontend-passing child
exists; two new tests cover initialization, failure retry and fixed-panel reuse.

v2 will be an explicit fork of v1, not an untouched independent trial. Its code
is separately frozen and its input cohort remains unchanged.

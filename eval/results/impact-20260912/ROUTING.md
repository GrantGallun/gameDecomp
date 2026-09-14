# Inconclusive comparison routing

The immutable campaign checkpoint contained 54 pending functions with differential
status `inconclusive`. The old queue silently assigned all of them to the byte
lane, including ordinary model polish. The receipts instead show unsupported
candidate execution or uncontracted direct-call ABI words, with no observed
source disagreement.

`solver.repair_queue` now routes these receipts to the existing environment lane.
Deterministic exact-object searches remain available; missing execution knowledge
does not become a source counterexample. Explicit observation signatures also
appear as shared, non-executable environment issues. Different missing causes or
incomplete/unknown signatures remain separate. Historical evidence fingerprints
are preserved, so deploying this policy cannot renew previously spent budgets.

Same-state replay (`routing-comparison.json`):

- 1,258 eligible functions before and after; identical eligible sets.
- All 2,051 evidence fingerprints unchanged.
- 54 functions / 34,548 target instruction bytes change lane.
- 19 currently queued model-polish profiles become remaining deterministic work.
- No completed profile is reopened; profiles outside these functions unchanged.
- Current next job remains `func_80061984` / `local_rewrites`.
- No verdict, compiler/frontend/semantic/exactness gate, fairness band, dependency
  ordering, model budget, or canonical source changed by this patch.

These are projected scheduling changes, not measured repair yield. No live
deployment was performed by this audit.

The largest shared obstruction is candidate `__ll_lshift` call arity (32
functions), followed by uncontracted `enqueueSoundEffect` arguments (12) and
candidate `__ull_rshift` call arity (2). Other exact reason signatures account for
the remaining eight functions. The shift helpers are absent from the target-call
closure used by `Panel`; their extracted ROM code also exceeds the existing
32-bit leaf dialect. They need a ROM-checked closed word-pair shift backend and a
panel-bound compiler-helper closure, rather than guessed arities alone.

Validation: `tests/test_repair_queue.py`, `tests/test_evidence_schedule.py`, and
`tests/test_investigation_workflow.py`: **48 passed in 6.10 seconds**.

`audit-routing.py` reads the content-addressed checkpoint through WSL and saves
the state as JSON; it never reads or copies the live attempt database.
`replay-routing.py` compares frozen and main queues on that saved JSON. Runtime
grafting requires only the `lane`, `evidence_key`, and `shared_issues` function
definitions from `solver/repair_queue.py`, not undeployed investigation features.

# (a) the engine's header module, and (b) the dashboard end to end

**Date:** 2026-09-17 · model calls 0 · byte-exact unchanged at 333

## (a) `project_headers` saw only braced typedefs — two places, one blind spot

The same class of bug I fixed in `eval/header_admission.declaring_header` existed in the engine's own
header module, twice:

| site | pattern before | consequence |
|---|---|---|
| `_typedef_definition` | `typedef (struct\|union) Name {` | a plain `typedef s16 Mat3x3[9];` returned None, so the definition was never offered to a draft |
| `dependency_headers` ("type" kind) | `(typedef )?(struct\|union) Name {` | a header declaring the type was not counted as a dependency, so the draft got no header |

The motivating case is real: `include/game/math/geometry.h:23` is `typedef s16 Mat3x3[9];` and
`drawRacePlayerModel-2`'s draft uses `Mat3x3 rotation;`.

Both now also match a no-brace `typedef ... Name ... ;` where the name is the **last identifier before
the `;`** (array suffix allowed), so `typedef s32 (*Fn)(RaceCourseSurface *);` is not read as declaring
`RaceCourseSurface`.

Verified by `tests/test_project_headers_typedefs.py` (7 tests), including two against the repository
rather than a fixture:

- `_typedef_definition` on the real `geometry.h` returns the definition whole
- `dependency_headers(REPO, "void f(void) { Mat3x3 *p; ... }")` now resolves `game/math/geometry.h`

A third test I wrote first was **wrong and I kept the finding**: I asserted
`relevant_declaration_context` would render the `Mat3x3` definition, and it returned `""`. That is its
documented scope — it collects `g`-prefixed globals (line 400) — not a bug. The test now drives
`dependency_headers` instead, and records the real remaining scope limit: `type_names` there is built
from `Type *` uses, so a draft writing `Mat3x3 rotation;` with no pointer is not a type use this
function sees at all. That is pre-existing caller scope, not something this change introduced.

## (b) The dashboard, launched and read

`eval/progress_app.py` serves the treemap from the campaign checkpoint, not from the attempt database
(`PIPELINE_MAP.md`: "It does not read the campaign attempt database"), with a 5-second projection cache
and a 10-second client refresh. Launched against the real `eval/results/resume-pipeline-20260908` run:

```
python3 -m eval.progress_app --run eval/results/resume-pipeline-20260908 --port 8791
GET /          -> 200,  27,583 bytes
GET /api/map   -> 200, 500,006 bytes
```

2051 functions, 675,200 target bytes, per-function `status`, `category`, `score`, `compiled`,
`attempt_id`, `work_items`.

| status | n |
|---|---|
| object_exact | 937 |
| pending | 1029 |
| parked | 42 |
| function_exact_pending_integration | 22 |
| integrated | 21 |

The map's own byte totals: `exact_bytes` 110,516 of `total_bytes` 675,200.

**Read that number as campaign state, not capability.** 937 `object_exact` against `eval/status.py`'s
333 byte-exact is not a contradiction: the checkpoint counts functions that reached object-exactness as
a work item, while `status.py` counts functions MATCHED into the build. `PIPELINE_MAP.md` says the same
("campaign state determines exactness colors"). Do not quote the treemap as the capability number.

`eval/map_report.py` was written to read a dumped map and diff two dumps, because the shell quoting
needed to grep JSON kept mangling and because "the map moved" needs a number behind it.

## What is NOT done, and why

I did **not** start the campaign service. `campaign_service --run DIR ensure` resumes a run directory,
and the only one on disk is a historical campaign (`resume-pipeline-20260908`) whose writer would touch
the real build tree; a scratch run directory needs a deploy step (`eval/results/*/deploy.py` copies
`launch.json`, `service.json`, `campaign.json`) that I have not validated. So the honest statement is:
**the dashboard is verified end to end and reads live checkpoint state; the service that would make it
move has not been started by me.**

To watch it move:

```
python3 -m eval.campaign_service --run <RUN_DIR> --batch 10 ensure   # supervisor
python3 -m eval.progress_app     --run <RUN_DIR> --port 8765         # dashboard
PYTHONPATH=. python3 -u eval/experiments/campaign-gap-audit/fresh_run_v1.py \
    --version 20 --model-calls 0 --per-stratum 12 --max-work-items 56
python3 -u eval/cohort_reconcile.py --ledger-glob 'failure-coverage-fresh-paired-v20-*.json' \
    --out eval/results/cohort-reconcile-v20-20260917
```

A map dump before and after, diffed with `eval/map_report.py --compare`, is what turns "it moved" into
a number.

## Suite

**3355 passed, 3 skipped, 1 failed** — the failure is the pre-existing environmental
`test_project64_trace::test_multi_job_validation_accepts_and_rejects`.

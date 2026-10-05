# Round 4: back to the loop that pays

**Date:** 2026-09-17 · **Model calls:** 0 · **Ratchet: 299 → 319 byte-exact, 233 → 253 SOLVED**

The goal's four work items are closed. Items 1–3 were answered in
`eval/results/admission-wall-provenance-20260917/RESULT.md`; this receipt covers item 4, the return to
the cohort loop, and the round's net.

## 1. The loop that pays, resumed: cohort v18

Single-flight, one cohort at a time (no other Python job running when it launched). The invocation
needs `PYTHONPATH=.` — without it the module import fails with `No module named 'eval'`, which is how
the first attempt of this round died.

```
PYTHONPATH=. python3 -u eval/experiments/campaign-gap-audit/fresh_run_v1.py \
    --version 18 --model-calls 0 --per-stratum 12 --max-work-items 56
```

Preflight passed with 1,225 exposed names and 233 selection receipts; two batches ran, the second
closing `paused_budget` with `{cohort_functions: 24, object_exact_or_integrated: 7, stalled: 5}`.

Reconciled **per settled node**, not per finished ledger:

```
python3 -u eval/cohort_reconcile.py --ledger-glob 'failure-coverage-fresh-paired-v18-*.json' \
    --out eval/results/cohort-reconcile-v18-20260917
```

| | n |
|---|---|
| settled nodes in the ledger | 14 |
| recorded | **14** |
| `REPRODUCED-EXACT` | **14** |

`fadeOutRaceRecordSettingsFlow`, `func_8005C3E4`, `initShopMenuCourseListPanel`,
`spawnPatrolCourseObject`, `startEndingSlashVanishBeforeExitRight`, `updateCourseGateClosing`,
`updateEndingCreditsTumblingSnowboardWaitForRemove`, `updateEndingSlashSlideLeftUntilPhase18`,
`updateFinalLapPrompt`, `updateMenuSpriteActorDebugControls`, `updateRaceIntroFlyoverIdle`,
`updateRacePlayerMode29Crash`, `updateRacePlayerMode32Character1`, `waitEndingJamPhase40`.

**Ratchet: 319 byte-exact / 253 SOLVED**, from 299 / 233 at the start of the goal. `header-assisted`
stayed 11 and `recovered` stayed 55, so the gain is not tier-shuffling — every one of the 20 is
object-verified in a tier that already existed.

Per-stratum 12 held at the recorded 17–25% band: 14 exact out of a 56-item budget, at zero model calls.

## 2. Item 2's honest route, resolved by measurement

The objective proposed an `unk`-only declaration plus a rewrite of the unresolved uses for types no
header declares. Measured, that is the wrong instrument, and §6 of the provenance receipt gives the
numbers: re-drafting all 31 affected drafts **context-free** removes the reference layout (verified: no
re-drafted source uses a project type as a type) and **0 of 29 compile**. The layout was doing the work.
An `unk`-only rewrite over the same offsets would launder the same knowledge rather than remove it.

So: header route for the 53 header-declared names, and for the 21 `.c`-only names there is no route from
m2c alone — the honest options are a model draft (no endpoint available) or the reference layout, which
is `header-assisted` at most and must be reported as such.

## 3. Verification

- `pytest tests/` — **3339 passed, 3 skipped, 1 failed**, the failure being the pre-existing
  environmental `test_project64_trace::test_multi_job_validation_accepts_and_rejects`.
- New tests this round: `tests/test_declaring_header.py` (7) — the four typedef spellings that must
  fire, and two neighbouring shapes that must not.
- `eval/status.py` re-read after the reconcile: 319 / 253 / 11 / 55, 52,786 attempts, 0 inference rows.

## 4. Open, and not this round's

- **The tier rule still keys on a strategy string.** `header-assisted` is
  `a.strategy like '%project-header%'`, so the header route's matches land in SOLVED. The honest
  numbers are **319 byte-exact, 253 SOLVED by the label rule, 251 by the source audit**. The whole-set
  check is specified in `patterns/catalog.py`
  (`header-assisted-tier-is-keyed-on-a-strategy-string-not-on-the-source`) and is the operator's call,
  because it lowers the headline number by correcting it. The ratchet protects byte-exact, which is 319
  either way.
- **`drawRacePlayerModel-2`** carries a numeric-suffixed workspace, which `workspace.bootstrap` refuses.
  Now reported as `skipped-suffixed-workspace` rather than raised; the draft is written either way.
- **The model-call path is dark**: no API key and no local endpoint in the WSL environment, which is why
  every number above is `--model-calls 0`. Admission's product — a scoreable draft at 80–98% — is only
  worth what a refine loop can do with it, and that loop has no engine here.

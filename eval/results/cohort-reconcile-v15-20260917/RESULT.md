# Rounds 6–7: the selector is NOT exhausting, and I misread a partially-written file

## v15 finished — nothing new, and one independent replication

`run_cohorts_r5.sh` completed. v15 batch 2: 24 cohort functions, **5 object-exact**, 1
`function_exact_pending_integration`, 1 parked, status `awaiting_integration` (every other cohort ended
`stalled_requires_new_strategy_or_evidence`). The closing reconcile returned **19 nodes, all
`already-counted`** — every one had been picked up by the mid-run reconciles in rounds 6 and 9, which is
what reading a running ledger buys. Ratchet unchanged at 287 / 221.

**The one node in the new state is `drawMainMenuModeSelectMenuOptions`**, and it is already in the
counted function-exact tier. Its certificate, produced independently by this fresh cohort:

    verification status      object_sections_differ
    exact                    False
    function_boundary        exact=True, schema=2, function_exact_pending_integration

That is the same verdict my own schema-2/3 audit reached, from a different run, on a different source
path. **Independent replication of the function-exact tier**, which matters because the tier's whole
justification is that the object-section test is unsatisfiable for a single-function candidate — and here
the campaign reached that conclusion on its own, twice.

Nothing here changes a number. It does mean the tier is not an artefact of one compile, and it is the
second time a fresh cohort has re-derived a verdict my tooling had already certified.

## Banked

v15 batch 1 reconciled for **+2**, both reproduced at 100.0 through the ordinary path:
`updateRacePlayerMode32Character5`, `updateRacePlayerPostUpdateMode22`.

| | round 5 end | now |
|---|---:|---:|
| byte-exact | 273 | **275** |
| SOLVED | 207 | **209** |

## Correction to round 6's caveat

Round 6 reported: *"v15's cohort collapsed to 2 nodes in batch 1 while v14's was 24 per batch at
per-stratum 8, so the selector may be exhausting the stratum space faster than the multiplier can
compensate."*

**That was wrong, and the cause is worth recording: I read `summary.cohort_functions` out of a JSON
file the campaign was still writing.** A settled read gives `n=24, exact=2`. The file is written
incrementally, so a mid-write read yields a missing `summary` and my reader reported it as absent rather
than as a partial file.

So the selector is not exhausting, the per-stratum multiplier is still the right lever, and the round-6
warning was a measurement artefact — the fourth time this session that a number came from a receipt
read before it was complete. The rule that catches it: a missing summary is not evidence of an empty
cohort, and a reader should say which it saw.

## In flight

`v16` and `v17` at `--per-stratum 12`, launched by `.cache/run_cohorts_r6.sh`, which ends by
reconciling v15 + v16 + v17 in one glob and printing `eval.status`. v15's batch 2 and v16's batch 1 are
mid-run as of this writing.

**Process lesson:** two cohort runs were in flight at once (v15 from round 5's script, v16/v17 from
round 6's). They write separate ledgers and separate code freezes, so no result is corrupted —
exactness is decided by the object comparison regardless — but they share the build workspaces under
`~/decomp/sbk1/nonmatchings/`, which is wasted work at best. One cohort job at a time from here.

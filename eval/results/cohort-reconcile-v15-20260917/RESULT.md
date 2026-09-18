# Rounds 6–7: the selector is NOT exhausting, and I misread a partially-written file

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

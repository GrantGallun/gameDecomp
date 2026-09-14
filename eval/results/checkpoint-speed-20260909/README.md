# Checkpoint performance repair

Measured September 9 against one paused 2,051-function campaign checkpoint on
the existing Windows-mounted run directory. All output variants parsed back to
exactly equal data. No model calls, test reductions, or changes to scheduling,
candidate verification, or per-item save frequency were needed.

| Writer | Seconds | Bytes |
| --- | ---: | ---: |
| Original pretty JSON, default buffer | 15.111 | 276,610,568 |
| Pretty JSON, 1 MiB buffer | 4.286 | 276,610,568 |
| Compact JSON, 1 MiB buffer | 3.153 | 133,027,045 |

The selected compact buffered writer is 4.79 times faster for checkpoint writes
in this measurement and writes 51.9% fewer bytes. This is not a 4.79 times
whole-pipeline speedup: compilation, semantic exploration, input verification,
database access, and process startup still take time.

The actual amended checkpoint subsequently saved in 3.3 seconds and passed a
full structural roundtrip comparison. Original data/code and before/after hashes
are archived under the campaign's `revisions/20260909-buffered-checkpoint`.
Forty-six checkpoint, service, repair, and campaign tests passed, including
preserving the prior valid checkpoint after a partial write failure and replaying
a durable completed receipt without executing the work again.

The campaign was resumed under the existing ten-work-item supervisor.
An early live check observed four completed work-item receipts after resumption:
the three start-to-start intervals were 17.751, 13.918, and 17.949 seconds.
The preceding 20 intervals had a median of 40.303 seconds. The initial post-change
median is 17.751 seconds, approximately 2.27 times faster. This small sequential
sample uses different functions and excludes a new batch boundary, so it is an
early throughput indication rather than a controlled end-to-end speedup estimate.
The existing native WSL game build directory is unchanged; no run-directory
migration or reduction in restart protection was necessary for this improvement.

Raw benchmark: [before-after.json](before-after.json).

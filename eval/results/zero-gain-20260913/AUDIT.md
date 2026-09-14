# Recent zero-gain repair audit

Read-only checkpoint **13035**, pointer SHA256
`996167d6cb16d6b846d0bfb1ffb87f4a7432488317944022652cf73ace3a0111`.
The latest 300 durable work receipts span 3,627.86 seconds. Receipt hashes,
source hashes, dispatch evidence keys, and proposal-ID mappings are in `audit.json`.
No live database was copied; SQLite queries used `mode=ro`. No campaign state was changed.

| Profile | Work items | Score improvements | Exact results | Source changed |
|---|---:|---:|---:|---:|
| local_rewrites | 164 | 2 | 0 | 161 |
| schema_patch | 70 | 4 | 2 | 65 |
| semantic_counterexample | 34 | 1 | 0 | 33 |
| deeper_composition | 12 | 0 | 0 | 6 |
| semantic_alternative | 11 | 0 | 0 | 10 |
| reasoned_alternative | 9 | 1 | 0 | 1 |

Of 292 zero-score-gain results, 268 changed selected source. Indexed function/source
lookups confirm **143 were include-only changes**, including 101 local rewrites.
Other measured ties include register annotations, unused locals, and typed versus
offset-based forms. These are real compiler experiments but do not justify replacing
the incumbent without a measured improvement. There were no identical
function/profile/source repeats within the 300-item window: changing source hashes
conceals the redundant search from the existing evidence-key budget.

All 364 mapped model proposal receipts were read by primary key: 214 valid,
68 invalid, 58 generation-error, 13 duplicate, 11 incomplete-response. All 58
generation errors were context-budget rejections before inference. That is a
separate remaining constraint; this fix does not relax context limits.

## Motivating paired compiler replay

`initCoursePreviewCloseSparkles`, canonical source receipts 90593 and 90594,
identical deterministic budget 32, model calls 0, and semantic panel of 64 cases.
Both runs used isolated native-WSL build trees and dedicated private attempt logs.
Only function/TU metadata and the two explicitly selected source receipts were read
from the live attempt DB. Full output, private lineage, and imported module hashes
are retained in the `replay-frozen-*` and `replay-staged-*` directories.

| Outcome | Frozen | Staged incumbent fix |
|---|---|---|
| Score | 84.226 | 84.226 |
| Semantic cases | 64 passed | 64 passed |
| Model calls | 0 | 0 |
| Selected source | Different, ten includes removed | Original preserved |
| Next repair profile | local_rewrites again | schema_patch |
| Evidence key | Changed | Preserved |

The production `accept`/`next_profile` projection is in
`replay-queue-projection.json`. The fresh measured root key
`9cb177ccd8d0f664247337d369bfddd6edf391c221c0f1e3becc54724c5790af`
matches the original live job key. The frozen result changes it to
`826da3428bcdc1ee5eb9307cead4b03ab43795a2108529b5b0ed7a5de9448f33`;
the staged result preserves it. This verifies the mechanism directly, rather than
assuming every zero-score change is waste or changing scheduler fairness.

## Project status scope

`status_readonly.py` runs `eval.status` with SQLite connections forced readonly.
The report at that read was 717 byte-exact functions of 2003 attempted:
608 SOLVED, 109 header-assisted, zero recovered from target source; 15 verified
matches existed only as files. These totals have different scope from checkpoint
cohort counts. The CLI's test count of zero is not a test-suite result.
See `status-readonly.log`; no capability claim is inferred from the treemap.

## Reproduction

Run `audit.py`, then `details.py` from the code root with PYTHONPATH set to the
desired code tree. These replace only the small audit outputs, and select a fresh
current immutable checkpoint each time. `replay.py --label LABEL` uses imported
code from PYTHONPATH and the explicitly fixed motivating source receipts.
`project-replays.py` compares the retained frozen/staged replay receipts without
writing any live state. Production deployment is handled separately by the root agent.

# Training panel: was the adapter trained on the panel the receipt cites?

| sha256 | file | role |
|---|---|---|
| `a393dc45f551f188…` | `dataset-txtoracle-ARCHIVED/tasks.jsonl` | what the adapter was **trained on** |
| `df5ea710ce1a522d…` | `evaluation-certified/frozen-panel/tasks.jsonl` | what the certified **evaluation** ran on |
| `13c727109da9f6cb…` | `dataset/tasks.jsonl` | the schema-3 re-freeze of the evaluation panel |

Task ids: **identical** (56 records).

**`child.exact` disagreements: 0.** This is the label the trainer filtered on
(`skipped_unverified_labels: 0` in its receipt means every train record was labelled exact),
so a disagreement would mean the adapter learned from a mislabelled example.

## Differing leaves, by class

- **determining**: none
- **method**: `child.certificate.error` differs in 56/56 records
- **method**: `child.certificate.exact` differs in 56/56 records
- **method**: `child.certificate.excluded[0]` differs in 56/56 records
- **method**: `child.certificate.excluded[1]` differs in 56/56 records
- **method**: `child.certificate.excluded[2]` differs in 56/56 records
- **method**: `child.certificate.kind` differs in 56/56 records
- **method**: `child.certificate.scope` differs in 56/56 records
- **method**: `child.certificate.status` differs in 56/56 records
- **method**: `child.verification` differs in 56/56 records
- **method**: `target.exactness_scope` differs in 56/56 records
- **artifact**: `parent.object_sha256` differs in 56/56 records
- **artifact**: `provenance.built_at` differs in 56/56 records
- **artifact**: `target.object_sha256` differs in 56/56 records
- **other**: none

## The 29 train-split records

Records differing in a label, input, answer, or an unclassified field: **0**.

## Verdict

**INTERCHANGEABLE FOR TRAINING.** The adapter's training records are the same
29 tasks, with the same `child.exact` labels, the same solver-visible inputs
and the same hidden answers as the current certificate panel. `child.exact` disagrees in
0 of 56 records, so rebuilding the dataset under the certificate did not relabel a
single example the adapter saw.

The only differences are **how verification was recorded** — the `.text`-era build
described its method in prose and carried no certificate block, while the certificate
build records a structured one — and the non-reproducible object digests and timestamps.
Neither is evidence about the training data.


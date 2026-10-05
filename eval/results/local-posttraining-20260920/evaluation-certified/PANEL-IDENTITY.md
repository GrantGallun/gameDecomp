# Panel identity: the certified panel vs the rebuilt schema-3 panel

The certified evaluation ran on the archived panel. The dataset was then rebuilt under the
schema-3 freeze, because a schema-2 manifest carries no per-record content hashes and the
hardened loader refuses it. This receipt asks whether those are the same panel.

| panel | records | bytes | sha256 |
|---|---|---|---|
| archived (certified run) | 56 | 388967 | `df5ea710ce1a522d…` |
| rebuilt (schema 3) | 56 | 388967 | `13c727109da9f6cb…` |

**Task ids: identical.** Same 56 ids, same families, same mutations.

## Experiment-determining fields

Every experiment-determining leaf is **identical** across all 56 records: the solver's
assembly, candidate and compiler feedback; the hidden answer; the target code image and
instruction count; the certificate verdict and its scope; the leakage report; the
compiler recipe; and the train/test split.

## Build-artifact fields (expected to differ)

IDO objects are not byte-reproducible: they embed the absolute source path and a random
`asm_processor` temp filename, so the same C compiled twice gives different `.o` bytes.
That is the reason this project compares sections and relocations through
`solver.byte_certificate.certify` instead of comparing object files, and it is the reason
the evaluation recompiles the target from the answer rather than trusting a stored digest.

- `parent.object_sha256` differs in 56 record(s) — raw `.o` bytes of the perturbed candidate
- `provenance.built_at` differs in 56 record(s) — wall-clock stamp of the build
- `target.object_sha256` differs in 56 record(s) — raw `.o` bytes of the answer

None of these fields is read by the measurement path. Searched `eval/evaluate_source_repair.py`, `eval/frozen_manifest.py`, `eval/train_source_repair.py`, `eval/posttraining_gate.py` for `object_sha256` and `built_at`:

```
eval/frozen_manifest.py:38: different object bytes: `parent.object_sha256`, `target.object_sha256` and `provenance.built_at`

```

## Verdict

**SAME PANEL.** The two files differ only in non-reproducible build artifacts: the same
56 task ids, the same solver-visible inputs, the same hidden answers, the same target
code images, the same certificate verdicts, the same splits. The certified measurement
therefore binds to the rebuilt schema-3 panel by content, and the rebuilt manifest's
`dataset_sha256` binds the file that was compared here.


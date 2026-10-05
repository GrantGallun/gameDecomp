# Adapter identity: is the recorded digest true of the adapter on disk?

`evaluation.json` recorded the adapter by reading the trainer's publish marker. A marker is
a claim, so the digest is recomputed from the files with the trainer's own `dir_digest`.

- recorded `adapter_sha256`: `4dab8f34e0ced96ec77d9e82dab7d80c712e38dbd909cddd50df310f36c4bb2d`
- adapter directory: `/home/grant/decomp/posttraining-20260920/adapter-smoke` (exists: **True**)

## Which directory reproduces the recorded digest?

| directory | files | `dir_digest` | reproduces? |
|---|---|---|---|
| `adapter-smoke` | 14 | `682db4fbdf9199adf15048ce47fcf018…` | no |
| `adapter-smoke/staging` | 6 | `4dab8f34e0ced96ec77d9e82dab7d80c…` | **yes** |

The published directory does not reproduce it because it has since gained a leftover
`staging/` copy and the `PUBLISHED.json` / `training_receipt.json` files written beside the
adapter. `dir_digest` covers names, sizes and bytes, so any added file changes it.

## Are the hashed files the published files?

Hashed directory: `adapter-smoke/staging` (6 files). Published top level: 8 files (the extra ones are the receipt and the marker).

| hashed file | bytes | sha256 | byte-identical to the published file |
|---|---|---|---|
| `README.md` | 5238 | `cac33ad387ab9e93e2475bd4a9277967…` | yes |
| `adapter_config.json` | 1197 | `cbf1344899ed6668b31830cad506295f…` | yes |
| `adapter_model.safetensors` | 80792096 | `7be4f7a02d477be31e87549b19208c47…` | yes |
| `chat_template.jinja` | 2507 | `cd8e9439f0570856fd70470bf8889ebd…` | yes |
| `tokenizer.json` | 11421892 | `3fd169731d2cbde95e10bf356d66d599…` | yes |
| `tokenizer_config.json` | 692 | `4d8ad3fe9729e37338fae50bac94ff03…` | yes |

- hashed but not published: none
- published but not hashed (receipt and marker): ['PUBLISHED.json', 'training_receipt.json']
- in both but different bytes: none

## What this artifact is

- base: `/home/grant/decomp/models/qwen2.5-coder-7b`
- LoRA: `{"r": 8, "alpha": 16}`, steps 7, split `train`, stop reason 'training loop exhausted'
- weights file `adapter_model.safetensors`: 80792096 bytes, sha256 `7be4f7a02d477be31e87549b19208c478a81017620154599c92327ea162d5271`

## Verdict

**VERIFIED.** The digest recorded in `evaluation.json` is `dir_digest` of
`adapter-smoke/staging`, and **every file it hashed is byte-identical to the
published file of the same name**. So the recorded digest does identify the published
adapter's contents, and the certified numbers are bound to an artifact that exists and
hashes to the recorded value. The first-pass mismatch was the leftover `staging/` copy
and the receipt/marker files beside the adapter -- not the weights.


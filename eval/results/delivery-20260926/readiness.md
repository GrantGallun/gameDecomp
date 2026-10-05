# Campaign delivery readiness (unapplied stage)

Historical readiness snapshot. The subsequent apply and bounded run completed;
see [DELIVERY.md](DELIVERY.md) for checkpoint 28396 and the current outcome.

The native WSL checkpoint remained at commit 28385 during this audit. Its durable
native pause marker was present. The frozen launch retains `model_calls=3`,
pipeline dispatch, `--integrate`, and `--runtime-plan`. Changing `--model-calls`
or removing the latter runtime options would fail resume identity checks. The
repeating supervisor is unsuitable for a 5-item canary because it starts another
batch after a successful budget exit.

The main controller now offers ephemeral `--deterministic-only`. It selects only
eligible non-model next profiles in both wave and pipeline dispatch, rejects
pre-existing model work in flight, skips model endpoint contact and drained
integration/runtime capture, and records its bounded session and stop reason.
It leaves stored config, model budget, work queue and unresolved status intact.
Focused WSL controller tests: **24 passed**. Dry amendment payload tests:
**2 passed**. Windows full-file controller tests encountered unrelated SQLite
file-replacement permission failures; the same file passed in WSL.

`revisions/20260926-delivery/stage.py` generated an unapplied stage from the
current main files at checkpoint 28385. It verified 3,255 unchanged pins and
scoped 9 changed code files, including the necessary named-parameter placeholder
fix; 47 additional pins come exactly from `binary_type_draft.input_paths` (one
target ELF and 46 m2c files; public headers were already pinned). It verified
shared m2c/repair/evidence dependencies byte-equal. The stage manifest SHA is
`97bdc1e5fd8825a38a97bd19771c701ae7972202ce02ecf200b25f68a60bcee8`.

`verify_stage.py` copied the frozen regular package tree to native WSL scratch,
overlaid only staged files, and supplied six fixed before/exact experiment
fixture pairs to tests only. Result: **163 passed, 1 skipped**. Its receipt is
`revisions/20260926-delivery/stage-test.json`; the first failing stage run was
preserved as `stage-test-before-dependency-fix.*`. `apply_amendment.py --validate`
confirmed all 9 staged code hashes and 47 additional input hashes. The
amendment and bounded-canary scripts were prepared but neither `--apply` nor the
live canary was run by this audit.

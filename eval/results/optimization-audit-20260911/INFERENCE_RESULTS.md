# Inference implementation and measured replay

The main tree now compacts known verbose observational lists in non-failing semantic reports. Unknown fields, binding obligations, call contracts, execution debt, source/panel identifiers and callee authority remain intact. Every compact packet includes the full report's deterministic hash and explicit omitted-item counts. Failure-directed prompting is unchanged. Full receipts are not modified.

The client rejects estimated context overflow before sending a request, instead of capping the requirement before checking it. Its UTF-8 bytes/2 estimate plus a 1,024-token template reserve includes the requested output allowance, prefill and conservative schema overhead. This is explicitly an estimate, not a tokenizer guarantee. Returned metadata records actual prompt count and whether the output allowance fit. Overflow exceptions are recorded by the repair worker, with no evidence truncation or reduced output budget.

Offline evaluation of the most recent twenty distinct-function saved repair prompts declined two original prompts, and one after compaction. The remaining declined prompt had previously exhausted its output allowance; no previously successful prompt in this small sample was newly declined. Proposal2408 shrank from72,306 to38,704 characters and now fits the estimate. No speed percentage is inferred from character counts.

## Effort replay

Twelve saved prompts, each replayed at high, medium and low effort with identical compact prompt, explicit seed, model, schema, temperature, 32k context, 6k output allowance and twelve CPU threads. Arm order rotates by function. Transport is bounded to one attempt with a240-second timeout. No response cache replay, server changes, concurrent model experiments or campaign imports occurred. Generation responses were durably saved before private compiler validation; interrupted compiler validation can resume without drawing another sample.

| Effort | Requests | Model request wall time | Generated tokens | Incomplete | Compiled **and** frontend passed | Exact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| High |12|632.379s|70,218|11|1|0|
| Medium |12|520.254s|57,208|4|4|1|
| Low |12|64.483s|5,626|0|6|0|

There were no transport errors. Applied candidates were1/high,7/medium,12/low. Compiler/frontend counts refer to direct proposed candidates before the live worker's further deterministic normalization. A shorter request is not necessarily a better repair: medium produced the only byte improvement and exact candidate, while low mostly tied or regressed the parent. High frequently exhausted its allowance without a final edit. These data do not justify replacing every higher-effort request with low effort.

The exact medium candidate is proposal2403, `initFixedTransform`, byte score95.192→100, frontend passing, with candidate hash `bc5972a2435a27988f74a693e092e0395183565dffd3e08124cf44f80166c20c`. The independent fixed-parent-panel validator preserved64/64 observed cases. The candidate is retained at `inference-replay-12/2403-compact-medium.c`; this benchmark did not import it. Root-agent follow-up handles any separate live admission.

The independent semantic audit is in `inference-replay-12/semantic-quality.json`. It uses one fixed target/header panel per saved parent for all arms, preserving finite-input scope and execution debt. It reports one medium byte improvement, no high/low byte improvements, no observed passing-case losses among comparable pairs, and explicit unavailable/inconclusive cases. Raw request summaries are in `inference-replay-12/request-summary.json`; raw results retain timing, token, request-option, extraction and compiler data.

## Full versus compact prompt controls

Two further low-effort pairs held seed and request settings constant and rotated original/compact order. The original2408 control explicitly permitted the old overflowing behavior inside this standalone benchmark only; the production guard has no bypass.

| Saved prompt | Original | Compact | Direct candidate outcome |
| --- | ---: | ---: | --- |
|2408 calculatePositionalSoundVolume|7.523s|4.380s|Identical candidate, both compile/frontend pass47.257; both regress the saved parent's byte score.|
|2403 initFixedTransform|5.699s|6.024s|Different candidates: original compiles/frontend passes71.571; compact compiles but fails frontend11.731. Both regress the parent.|

This tiny control establishes a faster identical proposal on one prompt and a quality regression on another. It does not establish a general useful-throughput gain from compaction. Context headroom and reduced repeated observational material are independently concrete improvements; ordinary verification remains essential. All paired results are in `inference-projection-pairs/` and were provided for the independent semantic audit.

## Files and validation

- Deploy `solver/prompt_budget.py`, `solver/llm.py`, and the three precise `solver/modelrepair.py` hunks. `stage_prompt_projection.py:apply_frozen(stage_dir)` verifies those hunks against a separate frozen staging tree and refuses the live code directory. It preserves unrelated frozen features.
- Focused Windows client/prompt suite:17 passed. Focused WSL prompt/client/modelrepair/ABI suite:46 passed in6.65s. Root handles the combined whole-project suite and deployment.
- `tests/test_prompt_budget.py` checks retained constraints/debt/unknown fields/provenance, no source mutation, full-receipt identity, unmodified failing/contradictory reports, pre-request overflow rejection, UTF-8/prefill/schema sizing and actual headroom diagnostics.
- `benchmark_inference.py` prepares a manifest without inference unless `--run` is passed. It requires a paused/drained campaign before every request, supports durable per-request resumption, and compares seed/settings identities on resume. Existing manifests are immutable input selections; use another `--out` directory to select another cohort.

The GPU was handed back to the root agent after all40 requests completed. No benchmark process or competing model request remains active.

## Optional bounded effort amendment

After reviewing the exact medium result, the root agent requested an explicit policy option in `eval/fast_campaign.py`: `--reasoned-effort profile|medium`, defaulting to the existing profile behavior. The opt-in maps only `reasoned_alternative` with requested high effort to medium. It retains profile name, evidence key, budgets, requested effort and policy in job profiles; semantic-alternative and low-effort profiles retain their settings. An absent legacy option means `profile`, and any change requires an explicit runtime amendment. Three added tests plus the existing controller suites passed:22 tests in9.21s. The root agent owns staged review and deployment; this agent changed no frozen/live file.

This is a bounded development policy trial motivated by one medium exact result versus no high exact result in twelve saved prompts. It is not a general claim that medium is superior, and it does not erase or replay previously consumed profile budgets.

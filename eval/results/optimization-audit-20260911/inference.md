# Inference audit — 2026-09-11

Read-only audit of the live RTX 5080 campaign. No inference benchmark was launched, no model/server setting changed, and the live campaign was not paused. Read DESIGN.md, PIPELINE_MAP.md, and CLAUDE.md. Source references below refer to main-tree files; the relevant prompt construction is also in the live frozen code. Counts are a small development/production observation, not campaign-wide estimates.

## Ranked findings

### 1. High-effort requests are spending their allowance on repeated reasoning without delivering an edit

The first eight completed requests after stable-context instrumentation spent 148.108 seconds in reported generation, 11.511 seconds in prefill, and 0.0228 seconds loading. Three length-stopped requests used 129.038 seconds of generation and 16,918 of 19,278 generated tokens. These are 87% of generation time and 88% of generated tokens **in this eight-request window**, not achievable savings or whole-run fractions. The report accumulated concurrently, so later requests are discussed separately below.

Telemetry `performance.model_requests[*]._prompt_sha256` in `resume-pipeline-20260908/pipeline.log` matches authoritative DB `model_proposals.prompt_sha256` for proposals 2403–2413. All five length-stopped responses in those eleven proposals were `incomplete-response`, `fell_back_to_thinking=true`, `phase=repair`. They contain unfinished reasoning, not a parsable final edit concealed by the extraction code. Four use the 6,000-token output allowance; one fills total context (finding 2).

Repeated twelve-word sequences appear 20 times in proposal 2406, 21 in 2408, 8 in 2409, and 12 in 2411. These include loops about the same pointer increment, stack offset, and candidate struct. Proposal 2413 also exhausts its reasoning allowance but is less repetitive. Merely generating these loops faster would not fix useful proposal throughput.

Current mechanism: `eval/completion_campaign.py:36` makes `reasoned_alternative` high effort; `solver/repair_queue.py:98` likewise makes `semantic_alternative` high effort. `solver/modelrepair.py:1033` already adds a completion handoff with 8,000 characters of unverified notes, and `:1062` changes that handoff to low effort with at most 4,096 output tokens, inside the existing logical-call allowance. This is **already implemented**. In proposals 2403–2413 the three completion handoffs produced two valid proposals and one invalid proposal. This does not establish that the preceding long reasoning was needed. A third/final high-effort request can exhaust the remaining logical-call budget without room for another completion.

Highest-value bounded experiment: replay 12–20 saved high-effort repair prompts across short/long functions and compile/semantic/byte lanes, using the same seeds, schema, 32k allocation and 6k output allowance. Compare high vs medium vs low effort, first holding prompt content fixed; record final-edit rate, incomplete rate, generated tokens, model seconds, compiling/frontend-passing edits, semantic changes and accepted score gains per minute. Preserve all raw responses. Choose effort by measured useful repair yield, not shortest response. A blanket cap reduction can remove the final answer while preserving the expensive reasoning prefix.

An independent experimental alternative is bounded loop detection with streaming, retaining partial output and using the existing low-effort handoff. `solver/llm.py:124` and `:128` currently request `stream=False`, so no early detection is active. This changes search behavior and requires controls on legitimate repeated code/assembly and cancellation cleanup before deployment. First replay the existing saved traces offline to measure how early a conservative repetition detector fires; do not equate repetitive text with invalid reasoning automatically.

Ollama documents low/medium/high effort for GPT-OSS and says its thinking cannot be fully disabled: https://docs.ollama.com/capabilities/thinking . The client already maps `think=false` to low at `solver/llm.py:78`; turning thinking “off” is not an unimplemented free switch.

### 2. Full semantic pass reports unnecessarily occupy much of the prompt and can consume final-answer headroom

`solver/modelrepair.py:1018` appends `json.dumps(parent.semantic)` whenever a semantic report exists and the candidate does not enter the observed-failure branch. That includes reports whose status is `observed_pass` or `observed_pass_with_execution_debt`. The entire stress-generation/coverage receipt becomes a repair objective, even with no differing observable. The failure branch already has a more selective `semantic_prompt` at `:526`.

Observed examples:

| Proposal | Prompt characters | Appended semantic section | API prompt tokens | API output tokens | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 2403, initFixedTransform | 31,510 | 16,220 characters | 10,463 | 435 | valid edit |
| 2408, calculatePositionalSoundVolume | 72,306 | 42,383 characters | 27,850 | 4,918 | length, reasoning only |

For 2403 the report's `stress_work` alone is 13,001 characters. For 2408 `stress_work` is 19,355 characters; target/candidate coverage lists total 13,034; exploration trials add 3,743. These include repeated missing-instruction lists, selected synthetic cases and discarded exploration examples. The complete reports remain valuable audit evidence; they do not all need to be transmitted on every proposal.

**Headroom defect:** `solver/llm.py:91–96` computes a heuristic requirement, then caps it at 32,768 before comparing explicit context. Therefore the explicit-context check does not guarantee the requested output headroom. Proposal 2408's 27,850 + 4,918 = 32,768 demonstrates total-context exhaustion despite a 6,000-token requested output allowance. In this request the character/3 heuristic plus output allowance is about 31,126, also underestimating actual token demand. Fixed context removed reload stalls but did not solve this pre-existing overflow.

Recommended projection for **non-failing semantic reports only**:

- Retain schema version; source_sha256, panel_sha256, semantic_key; deterministic full-report hash/receipt reference; status and authoritative flag; counts/total and compact outcome_accounting.
- Retain all execution debt and its scope/authority caveats. Preserve inconclusive-reason groups and omission counts. Never recast finite observed pass as proof.
- Retain source_object_obligations, callee_source_contracts, call_contracts, opaque_stack_obligations, indirect_call_obligations, unknown_direct_argument_evidence, execution_obstructions, and any other source/ABI binding constraint. Do not silently truncate constraints to fit a character target. Oversized binding evidence needs explicit selection/retrieval or a conservative overflow outcome.
- Retain callee authority, assumptions and implementation/provenance hashes; remove duplicated program/extraction transcripts only where their operative contract is preserved.
- Replace stress_work selected-case payloads and enumerated attempted mutations with status, selected/generated/attempted counts, status counts, work limits, stop reasons and omitted-item counts, retaining their full-report hash.
- Replace coverage instruction/edge enumerations with status, model version, covered/total counts, fractions and unresolved-edge/indirect-jump counts. Keep debt and known limits explicit; do not fabricate coverage of omitted entries.
- Include a failing observable/operation gradient if present; preserve existing full failure-directed prompting initially. A pass report should state that there is no observed failing repair objective.

Tests should assert provenance, debt and binding-obligation retention, no source-report mutation, deterministic identity, and that observed-failure prompting is unchanged. Replay saved 2403/2408 reports with the projection to measure actual token count and useful output behavior; a character-count reduction is **not** a speedup claim. Consider token-aware preflight or a conservative calibrated estimate with clear overflow diagnostics, never silently dropping CURRENT C/target evidence or merely lowering output allowance.

### 3. Prompt-prefix preservation is present in the server but frequently unavailable across requests

Live server stderr under `gpu-pipeline-20260911/*153213-server*.log` explicitly reports `forcing full prompt re-processing due to lack of cache data (likely due to SWA or hybrid/recurrent memory)` and invalidated context checkpoints. The latest observed request reprocessed approximately 7,420 prompt tokens. In the measured first-eight window, prefill is only 11.5 seconds versus 148.1 seconds generation, so perfect prefill elimination cannot remove the dominant cost in that sample.

The repair loop rebuilds the entire prompt each call (`solver/modelrepair.py:997`), adding current source, residual, slots, headers, semantic material and strategy. Completion calls append notes to this rebuilt prompt; they are not compact continuation messages. Fixed context and existing server cache already help. A model lease per request also permits another function to replace the previous function's prompt state between related calls (`eval/fast_runtime.py:94`). Holding a lease for a whole job may help locality but can starve ready GPU work while the owner compiles; it is not an obvious win.

Bounded experiment after compact semantic packets: replay alternating versus adjacent requests for the same function, inspect actual prefill durations and cache-prefix logs, then test ordering immutable instructions/target evidence before mutable state. Preserve all content for the layout-only arm. Do not enable a locality scheduler or reduce fairness based merely on common-prefix character counts. A larger SWA cache or different backend could change memory and cache behavior and needs its own fit benchmark.

### 4. Speculative decoding is plausible but not currently a demonstrated gain or a simple verified Ollama switch

Current deployment is a single fully GPU-resident MXFP4 model, Flash Attention, f16 KV, one inference slot, fixed32k context; prior experiments already rejected q8/two-slot concurrency as effectively flat aggregate token throughput with no demonstrated quality advantage. Repeating those settings is lower priority than the concrete reasoning/headroom issues.

Official llama.cpp supports model-free n-gram speculation and learned draft approaches. Its documentation lists `ngram-simple` for repeated text/code and `ngram-mod` with a shared approximately16MB hash pool; it also describes GPT-OSS-compatible EAGLE-3 draft support. Source: https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md . These are llama-server facilities; availability through this installed Ollama0.34.0 NVIDIA API was not established. The project has no configured speculation options in `solver/llm.py`. Ollama's separate MLX implementation is not evidence of a Windows CUDA feature.

First benchmark should be an isolated, pinned llama-server build using the same target model bytes, rendered chat template, reasoning effort, seed, schema, sampling settings and fixed allocation, baseline vs model-free n-gram speculation. This avoids spending scarce VRAM on a second large model. Record accepted/drafted tokens, prefill/decode durations, peak VRAM, final-edit validity and compiler/semantic outcomes. Test both reasoning-heavy traces and short source edits, with varied prompts and multiple orders; repetitive reasoning could give a high token-rate score while remaining useless work. Only then test a compatible small EAGLE draft if memory fits. Changing providers must preserve proposal receipts and invalidate cache/runtime identities; numerical/output differences require yield evaluation.

## Measurement caveats and next decision

The stable-context benchmark established removal of reload overhead, not faster decode or preserved quality. Current evidence makes compact non-failing reports plus explicit answer headroom the clearest correctness/throughput improvement to implement and validate first. High-effort selection is the largest measured inference-time opportunity but remains a quality-sensitive experiment. Prefix and speculation work are independent follow-ups; do not promise a speed multiplier before replay results.

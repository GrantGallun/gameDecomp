# Throughput optimization audit — September 11, 2026

Three independent audits found additional opportunities in inference, controller/storage work, and compiler/semantic reuse. This was a read-only audit of the running campaign: no live code or settings changed, and no competing GPU benchmark ran.

## Priorities

| Priority | Change | Evidence | Next validation |
| --- | --- | --- | --- |
| 1 | Compact non-failing semantic reports and enforce answer headroom | One prompt included 42,383 characters of semantic-report material out of 72,306 total. Its 27,850 prompt tokens left 4,918 output tokens before the 32,768 context filled; the response never emitted a final edit. | Preserve report identity, execution debt, source/ABI obligations and provenance; replay saved reports and measure actual token use and verified edit yield. Keep failure-directed evidence intact. |
| 2 | Copy only missing run-metadata rows when synchronizing workers | Every dispatch rereads the full attempt_runs payload. A bounded read of 5,025 rows transferred 19.36 MB in 1.283 s. An earlier covering-index ID scan took 0.0023 s. | Benchmark actual synchronization on identical DB copies; prove complete historical row equality, orphan cleanup, ID remapping and crash replay. Read timing is not a measured end-to-end saving. |
| 3 | Test reasoning effort against useful repair throughput | Three incomplete requests consumed 129.0 of 148.1 seconds of generation in the first eight completed requests after instrumentation. Later trace inspection confirmed repetitive unfinished reasoning. | Paired high/medium/low effort replay across 12–20 saved prompts. Compare accepted improvements per minute, final-edit rate, compilation and semantic outcomes. This small window does not establish whole-run waste or achievable savings. |
| 4 | Remove redundant filesystem checks while retaining complete hashing | Each job verifies 16,095 pinned files before dispatch and import. Exact verification took 1.636 s/pass; reading and hashing without the extra is_file check took 1.100 s/pass. | Benchmark single-open, descriptor-validated full-byte hashing. Test changed content with restored timestamps, deletion, directory replacement and symlink changes. Do not replace content validation with timestamp checks. |
| 5 | Reuse freshly verified identical compilations within a job | Of 1,000 recent attempt rows, 250 repeated a source for the same function, including 46 within one run. Code also recompiles a freshly compiled deterministic baseline. | Start with same-job reuse bound to all build inputs and immutable artifacts, preserving lineage and acceptance gates. Duplicate source alone is insufficient: 40 groups had differing serialized recipes. |
| 6 | Measure and cache semantic-panel construction | Current semantic caching starts after target exploration and stress-case generation. Construction time is absent from the semantic-call metric. | Instrument construction, then compare cold/warm cases, identities, debt and contracts. Bind target, ABI, budgets, environment and runner version. |

## Further experiments

- Persistent workers could avoid about 0.55 s of fresh import overhead per job. First fix global wrapper lifetime and per-job database authorization; merely increasing tasks per child is unsafe.
- Stable compilation workspaces could improve the observed 35 hits / 1,955 misses, but filename-sensitive compilation, relative includes, debug/source attribution and worker paths require explicit handling.
- Prefix locality may reduce prefill. In the eight-request sample, prefill was 11.5 s versus 148.1 s generation, making it a smaller immediate target.
- Model-free speculative decoding is a plausible isolated backend experiment. [Official llama.cpp documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md) describes n-gram options. Support through the installed Ollama API was not established; no throughput gain is claimed.
- Native WSL placement of remaining I/O hot paths may help. [Microsoft recommends the Linux filesystem for Linux tools](https://learn.microsoft.com/en-us/windows/wsl/filesystems). Measure the smaller synchronization fix first; relocating the authoritative DB requires coordinated migration and a single writable authority.
- Longer controller sessions could reduce batch draining and supervisor restart delay after per-job overhead is addressed.

## Measurement and safety

The sampled GPU had 94% utilization, 57 C temperature and roughly 202 W power draw, with no active thermal or power throttling. Increasing power limits has no demonstrated benefit here. Useful accepted repairs per elapsed hour should determine success; token speed and GPU utilization alone do not.

Controller metrics currently omit import-side work, worker timing excludes process initialization, and elapsed-session/rate metrics update only at batch completion. Add stage timing and live elapsed time before claiming end-to-end gains. Worker durations overlap and must not be summed into a speedup estimate.

Keep full frozen-input verification, complete worker history, durable exactly-once imports, proposal receipts, compilation, frontend, semantic and exactness gates. Do not skip work by function/profile alone. Do not cache target execution solely by panel/case: candidate symbols and data currently affect the derived execution context.

Recommended implementation order: compact report/headroom handling and stage timing; incremental run-row synchronization and single-open hashing; same-job compilation reuse; then quality-sensitive effort and broader cache experiments. All performance estimates above remain bounded observations until controlled replay and live elapsed-time validation.

## Evidence

- [Inference audit](inference.md): request/proposal identities, prompt fields, reasoning traces and replay plans.
- [Controller audit](controller.md): pin inventory, synchronization reads, per-stage timings and migration constraints.
- [Reuse audit](reuse.md): bounded attempt IDs 33515–34514, code paths and cache correctness constraints.
- [Live snapshot](live-snapshot.json): completed-job metrics after stable-context deployment; excludes unfinished requests and jobs.

# Stable-context throughput experiment — September 11

Live Ollama 0.34.0 runner logs showed repeated 16,384 / 32,768 context changes
reloading the full model, each startup taking about 32 seconds. The old adaptive
client saved KV memory for small prompts but caused expensive model churn when
the rolling scheduler alternated functions of different sizes. The previous
fixed-32k inference microbenchmarks did not exercise this production failure.

The fix keeps the dedicated campaign server at 32,768 context for every request.
It retains model weights, f16 precision, existing prompts and seeds, 6,000-token
output budgets, one inference slot, and all acceptance gates. Standalone client
callers retain the previous adaptive default; an explicit context cannot reduce
the former headroom or exceed its ceiling. Context is part of generation cache
identity, so old responses cannot masquerade as new-setting measurements.

Every campaign worker now records request options, prompt hash, cache status,
loading/prefill/generation durations, token counts and completion reasons.
Aggregate counters distinguish model loading from actual inference. Cached
responses do not increase measured token or device-time counters.

## Paired replay

`benchmark.py` alternates recorded proposals 2382 (`__osPopThread`) and 2353
(`renderRacePickupRespawn`) twice, using the same saved prompts, seeds,
temperature, schema, CPU thread setting and 6,000-token output allowance in
both arms. Each arm starts with a warmed 32k model; warmup is retained but not
counted in the request total. Adaptive requests use 16k/32k/16k/32k. Fixed
requests use 32k throughout. No response-cache replay is enabled. Both arms
may reuse internal prompt prefixes normally. All outputs and timings are
retained, and `validate.py` compiles applicable edits in isolated native
workspaces. No benchmark candidate is imported into the campaign.

`comparison.json` is the measured result, including candidate identity and
compiler/frontend outcomes. This is a four-request development replay,
not an estimate of whole-game completion time or a statistical quality study.
Savings depend on how often successive production requests would change context.

| Four-request replay | Adaptive context | Fixed 32k |
| --- | ---: | ---: |
| Request wall time | 203.635 s | 8.215 s |
| Reported model loading | 117.541 s | 0.009 s |
| Generated tokens | 1,352 | 927 |
| Applicable / compiling / frontend-passing | 4 / 4 / 4 | 4 / 4 / 4 |

Measured request-wall ratio: **24.79x**. This includes avoiding unload/reload
delays and retaining useful prompt-cache state, not faster token-generation
kernels. The prompts, seeds and output allowances match; the actual generations
and token counts differ. Only one candidate pair is text-identical. Three pairs
have equal byte scores; the remaining fixed-context candidate scored lower
(25.833 versus 34.444). No candidate in either arm is exact. The ordinary
campaign acceptance gates remain responsible for quality; this experiment
establishes removal of reload stalls rather than identical repair yield.

The preliminary `adaptive-small-only.json` used two prompts that both selected
16k. It exposed one initial reload but did not test repeated alternation, so it
is retained separately and excluded from the paired result.

## Validation and deployment

Full regression suite: **2,154 passed in 45.45 seconds**. Tests include explicit
context cache isolation, rejection of insufficient context, stable campaign
allocation, cached-response cost accounting, prior transport behavior,
crash-safe worker imports and rolling scheduling.

Deployment is performed by `deploy.py` at a drained checkpoint. Revision
`../resume-pipeline-20260908/revisions/20260911-stable-model-context/` preserves
previous modules, launch state, a standalone rollback checkpoint, hashes and
the authorization receipt. The shared client also carries the already-tested
optional transport-attempt argument from the main tree; its default remains
three, exactly matching the old frozen behavior.

After deployment the campaign resumed successfully. Five live `/api/chat`
requests completed with HTTP 200, the loaded context remained 32k, and the
server had **zero new runner starts** since resumption. CPU work continued
committing. `live-validation.json` retains this initial observation and the
server response log tail; this is not a whole-batch throughput estimate.

Remaining independent avenues include reducing repeated prompt material and
testing speculative decoding. They require their own workload/quality studies;
the present change targets the measured reload overhead directly.

# RTX 5080 memory and scheduling experiment — 2026-09-11

Subsequent correction: the live client now fixes context at 32k to avoid
Ollama reloads when adaptive sizes change. The earlier inference tests below
held context constant and missed that production overhead. See
[the paired context experiment](../context-throughput-20260911/README.md).

The live campaign now uses rolling dispatch with three isolated workers: two
eligible model profiles prepare work for one serialized inference slot, while
the third favors CPU work. Completed workers import immediately and refill;
the controller no longer waits for a whole wave. Within each resource lane,
evidence-v1 priorities and original next profiles remain authoritative. No
per-function call budget, acceptance gate, model weight or game source changed.

The dedicated Ollama server on Windows port 11435 uses Flash Attention, f16 KV,
one loaded model, one inference slot and a 30-minute idle expiry. It retains
the existing adaptive request context and 32,768-token ceiling. Only this
server holds the campaign model; the old desktop endpoint was unloaded.
`launch-campaign-gpu.ps1` contains process-local settings, leaving the user's
desktop Ollama environment unchanged. Run it with PowerShell's per-process
`-ExecutionPolicy Bypass` if the default script policy requires that.

## Why quantization was tested but not retained

Ollama 0.34.0 was already automatically enabling Flash Attention. GPU runner
logs measured 786 MiB of f16 KV at 32k, versus 417.56 MiB with q8_0 (46.9%
smaller). Two q8 slots consumed 835.13 MiB, keeping all 25/25 model layers on
GPU. This is cache compression, not removal of model weights or experts.

| Equal-token development microbenchmark | Generated tokens | Wall time |
| --- | ---: | ---: |
| f16, one request at a time | 4096 | 32.130 s |
| q8_0, two concurrent requests | 4096 | 31.877 s |

Each test replayed the same saved long prompt four times at high reasoning
effort with a deliberately short 1024-token cap. These are throughput probes,
not candidate-quality tests. Prefix-cache reuse makes them microbenchmarks,
not independent campaign speed estimates. The <1% difference is not a useful
win. q8 lowered individual generation throughput. Two full-precision slots
caused severe memory pressure and very slow prefill despite a nominal
all-layers-on-GPU fit; that run was stopped and its log preserved. An accidental
overlapping baseline launch after a failed restricted process-stop was also
stopped; neither interrupted run is included in the table. The final baseline
was rerun with only one model resident.

Separate four-prompt, 6000-token-budget proposal replays yielded 3/4 parsable,
applicable, compiling candidates for f16/one, 2/4 for q8/one, and 1/4 for q8/two.
These tiny samples do not establish a general quality difference; they provide
no reason to trade precision for essentially unchanged measured throughput.
All raw responses, failures, sources and compiler/frontend reports are retained.
Nothing from these experiments was imported into the live campaign.

## Validation and deployment

- Full regression suite: 2152 passed in 59.97 seconds.
- Final scheduling and dashboard checks: 19 passed in 10.60 seconds.
- Tests cover resource preference, no duplicate function dispatch, out-of-order
  completion, slot reuse only after import, pause/drain, bounded inference leases,
  lease release after failure, exact-once imports and checkpoint integrity.
- Revision: `../resume-pipeline-20260908/revisions/20260911-gpu-pipeline/`.
  Includes old files, previous launch/config, a full standalone rollback
  checkpoint, new frozen file hashes and explicit authorization receipt.
- Batch size increased from 10 to 50 to amortize checkpoint hydration/startup;
  safe pause is checked before every dispatch, so this does not lengthen the
  pause boundary to 50 tasks. Initial preparation of the third private database
  is a one-time copy; later synchronization remains incremental.
- Dashboard at http://127.0.0.1:8765 shows GPU activity, total allocated VRAM
  including other apps, and power draw. Sampling occurs every five seconds on
  a background thread and does not block HTTP polling.
- Post-startup live observation: approximately 79% mean GPU activity across a
  30-second polling window, peak total allocated VRAM 14,885 MiB, and two CPU
  work items committed while model work continued. The dashboard supplies a new
  hardware sample every five seconds; these polls are not 30 independent GPU
  measurements. `live-validation.json` preserves the window and
  `startup-validation.json` preserves the earlier startup/idle observation.
  This confirms overlap and ongoing commits, not a whole-campaign speedup.

Reference: [Ollama FAQ](https://docs.ollama.com/faq) documents cache types,
Flash Attention, model residency and concurrency memory costs. Actual decisions
above use this machine's measured logs and retained experiment receipts.

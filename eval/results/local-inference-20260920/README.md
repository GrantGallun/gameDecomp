# Local inference service — Qwen2.5-Coder-7B (+ LoRA) over an OpenAI-compatible API, with generation receipts

Run date **2026-09-20**. Host: Windows + WSL2 Ubuntu (kernel `6.18.33.2-microsoft-standard-WSL2`),
NVIDIA **RTX 5080 16 GB**, driver 610.88, GPU *shared* with a Windows-side ollama server
holding 2.2–3.8 GB. All model state lives on the WSL filesystem (`/home/grant/decomp/`), never `/mnt/c`.

Code: `tools/lora_serve/` (new files only; nothing under `solver/`, `kb/`, `eval/`, `tests/` was modified).

---

## 1. Exact serve commands

### 1a. Base-only arm (M0) — port 8101 — **recommended, vLLM backend**

```bash
bash tools/lora_serve/serve_vllm.sh
```

Expanded (what the script runs, env vars included because they are *required* on WSL2):

```bash
cd /mnt/c/Code/gameDecomp
export HF_HOME=/home/grant/decomp/hf-home
export VLLM_USE_V2_MODEL_RUNNER=0       # WSL2 has no UVA; the V2 runner raises "UVA is not available"
export VLLM_USE_FLASHINFER_SAMPLER=0    # else the first sample JIT-compiles FlashInfer and needs nvcc
export CUDA_HOME=/home/grant/decomp/serve-venv/lib/python3.12/site-packages/nvidia/cu13
/home/grant/decomp/serve-venv/bin/python -m tools.lora_serve.server \
  --model /home/grant/decomp/models/qwen2.5-coder-7b \
  --backend vllm --quantization fp8 \
  --port 8101 --default-adapter none \
  --max-model-len 12288 --max-num-seqs 6 --gpu-memory-utilization 0.72 \
  --max-batch-size 6 --max-batch-tokens 60000 --max-batch-prefill-tokens 48000 \
  --receipt-log ~/lora_serve_receipts.jsonl
```

Measured startup: engine ready in **18.7 s**, warmup 0.23 s, `vram_free_gb 13.85` afterwards.

### 1b. Adapter arm (M1) — port 8102

```bash
ADAPTER_PATH=/path/to/adapter PORT=8102 bash tools/lora_serve/serve_adapter_arm.sh
```

which is the same command with `--adapter m1=$ADAPTER_PATH --default-adapter m1 --port 8102`.

### 1c. **Both arms from ONE process** (best transport control; verified, see §6d)

```bash
bash tools/lora_serve/serve_vllm.sh --adapter m1=/path/to/adapter --default-adapter none
#   model id "qwen2.5-coder-7b"     -> base checkpoint      (M0)
#   model id "qwen2.5-coder-7b+m1"  -> base + LoRA m1       (M1)
```

Identical process, identical quantisation, identical batching; the only difference between
arms is the `model` field of the request. Verified with `tools/lora_serve/two_arm_check.py`.

**Only ONE GPU-resident server fits on this card.** fp8 weights ≈ 7.5 GB (4-bit ≈ 5.4 GB) against
16 GB shared with ollama, so 8101 and 8102 cannot both hold the GPU. Serve the arms sequentially,
or use 1c.

### 1d. transformers fallback (no vLLM) — port 8101

```bash
PORT=8101 bash tools/lora_serve/serve.sh run     # --backend transformers, bitsandbytes NF4 4-bit
```

Slower (see §4) but needs no vLLM and no CUDA-toolkit workarounds.

### 1e. State as of this writing

A **base-only** vLLM server is running on `http://127.0.0.1:8101`: `/health` reports
`"default_adapter": null`, `"adapters": {}`, and `/v1/models` lists exactly one id
(`qwen2.5-coder-7b`, `default: true`). No adapter is registered, so no request can reach one.
The parent's failing case now measures **1.16 s** (127-token prompt, `max_tokens 200`,
`prefill="```c\n"`, `echo_prefill=true`, `finish_reason stop`, `queue_ms 25`) where the
transformers backend under memory pressure took 390 s.

---

## 2. Exact client call

```python
from tools.lora_serve import InferenceClient

client = InferenceClient("http://127.0.0.1:8101", timeout=1800)

result = client.chat(
    [{"role": "user", "content": prompt}],
    prefill="```c\n",              # partial ASSISTANT turn (the refusal-suppression mechanism)
    temperature=0.2, top_p=0.8, top_k=20, seed=1000,
    max_tokens=3000, repetition_penalty=1.1,
)
result.text                        # prefill + continuation
receipt = result.require_receipt() # raises if the server did not return one
receipt["prompt_tokens"]; receipt["wall_ms"]; receipt["rendered_prompt"]
receipt["adapter_hash"]; receipt["sampling"]; receipt["stop_reason"]
```

Adapter arm: `client.chat(..., model="qwen2.5-coder-7b+m1", ...)`.
Receipt fetch by id: `client.receipt_for(result.request_id)` (HTTP `GET /receipts/<id>`).

Raw HTTP equivalent:

```bash
curl -s http://127.0.0.1:8101/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "messages": [{"role": "user", "content": "<PROMPT>"}],
  "prefill": "```c\n", "temperature": 0.2, "top_p": 0.8, "top_k": 20,
  "seed": 1000, "max_tokens": 3000, "repetition_penalty": 1.1, "receipt": true}'
```

---

## 3. Receipt contents (hard requirement)

Every response carries `id` (request id, in the `X-Request-Id` header too) and, when
`"receipt": true` is requested, an inline `receipt` object. Each receipt is also appended to
`~/lora_serve_receipts.jsonl` and retrievable at `GET /receipts/<request_id>`.

| field | meaning |
|---|---|
| `schema` | `gameDecomp.local_inference.receipt/1` |
| `request_id`, `created`, `endpoint` | identity of the call |
| `model` | path, `config_sha256`, architectures, shard names/sizes, checkpoint `generation_config` |
| `adapter`, `adapter_hash`, `adapter_requested` | adapter path, combined sha256 over every adapter file, and what was asked for |
| `rendered_prompt`, `rendered_prompt_sha256`, `rendered_prompt_chars`, `rendered_history`, `prefill`, `templated`, `template_source`, `messages` | the exact text handed to the model, prefill included |
| `prompt_tokens`, `rendered_prompt_tokens`, `prompt_token_ids_sha256`, (`prompt_token_ids` if asked) | exact token count / ids |
| `completion_tokens`, `total_tokens`, `raw_response_text`, `continuation_text`, `raw_response_sha256`, `chars` | exact output (prefill and continuation separated) |
| `finish_reason`, `stop_reason`, `choices[]` (per-choice finish/stop/generated/returned tokens, `timing_source`) | why generation ended |
| `sampling` | temperature, top_p, top_k, repetition/frequency/presence penalty, seed, **plus `*_source` (request vs server default)**, penalty scope, stop strings |
| `timings_ms` (`queue_ms`, `tokenize_ms`, `prefill_ms`, `decode_ms`, `total_ms`), `wall_ms`, `tokens_per_second` | wall-clock accounting |
| `server` | backend, quantisation, dtype, device, served model ids, batch limits, pid, start time, env workarounds |
| `batch` | batch size and the request ids it shared a batch with |

`timing_source` is `vllm_metrics` when vLLM's own per-request timestamps were available and
`batch_wallclock` otherwise — in the latter case `prefill_ms` is 0.0 and `decode_ms` is the whole
batch wall clock, never a guess presented as a measurement. On this host the vLLM metrics were not
populated (`timing_source: batch_wallclock`), so **treat `prefill_ms`/`decode_ms` as a single
combined generation time for the vLLM backend**, and use `wall_ms` for budgeting.

Extension fields (documented, and unknown fields are rejected with an error rather than ignored):
`prefill`, `continue_final_message`, `echo_prefill`, `receipt`, `receipt_token_ids`,
`add_generation_prompt`, `top_k`, `repetition_penalty`, `chat_template`.

---

## 4. Measured throughput

All numbers below are from `throughput.json`, `bench-9k-token-prompt/throughput.json` and the
receipts beside them. Sampling for every run: `temperature 0.2, top_p 0.8, top_k 20,
repetition_penalty 1.1`, `prefill="```c\n"`.

### 4a. vLLM backend, fp8 weights (recommended)

| prompt | concurrency | wall | output tok/s (aggregate) | prompt tok/s | per-request wall |
|---|---|---|---|---|---|
| 11,986 chars = **5,416 tokens**, `max_tokens 3000` | 1 | 6.10 s | **74.96** | 888 | 6.09 s (stopped at 457 tokens) |
| same | 6 | 82.81 s | **157.95** | 392 | 4 rows 41.3 s (batch 4) + 2 rows 82.8 s (batch 2, 41 s of it queued) |
| 20,490 chars = **8,890 tokens**, `max_tokens 3000` | 1 | 14.87 s | **73.86** | 598 | 14.86 s (stopped at 1,098 tokens) |

Decode is a steady **≈74–80 output tokens/s per stream**; concurrency 6 roughly doubles aggregate
throughput (158 tok/s). Two of the six seeds hit EOS early and returned fewer tokens, which is why
the median (41.3 s) is below the max (82.8 s).

### 4b. Budget for a bounded run (per-request, concurrency 1, vLLM)

* A draw whose answer runs the full **3,000 tokens: ≈ 41 s** (3,000 / 74) + prefill.
* A draw that stops at ~1,100 tokens (measured on the 8,890-token prompt): **14.9 s**.
* **500 sequential draws: ≈ 2 hours if answers average ~1,100 tokens; ≈ 5.7 hours if every draw
  runs the full 3,000-token cap.** Budget **≈ 6 h** for 500 draws at `max_tokens=3000`, and expect
  concurrency > 1 to cut that roughly in half (aggregate 158 tok/s at 6-way).
* Prompt token density is **≈2.2 characters per token** for MIPS listing text, so 12,000 chars ≈
  5.4 k tokens and a 9,000-token prompt needs ≈20 k chars. `--max-model-len 12288` covers
  9,000-token prompts + 3,000-token answers exactly; raise it (and expect more KV memory) for more.

### 4c. transformers fallback (4-bit NF4, bitsandbytes) — measured, much slower

| prompt | concurrency | wall | output tok/s | notes |
|---|---|---|---|---|
| 5,416 tokens, `max_tokens 3000` | 1 | 173.9 s | **17.25** | decode 170.3 s for 3,000 tokens |
| same | 6 | 402.0 s | **35.52** | one 6-row batch, 390 s |
| 5,416 tokens, `max_tokens 1500` (sample) | 1 | 15.9 s | **41.26** | stopped at 652 tokens |

The same 500-draw run on this backend is **10–24 hours**. Use vLLM.

### 4d. Concurrency limits observed

* vLLM, 5.4 k-token prompts, 6 concurrent: all six served, two batches (4 + 2) because the server's
  `--max-batch-prefill-tokens 48000` and `--max-batch-size 6` split them; a queued row waited 41 s.
* transformers backend, 6 concurrent 5.4 k-token prompts: one batch, but it drove free VRAM to
  **0.0 GB** (`vram_allocated_gb 15.157`), which is what starved a concurrently-queued request
  (see §7). The server now bounds a batch by summed prompt tokens
  (`--max-batch-prefill-tokens`, default 12000 for that backend) so this cannot recur.

---

## 5. Checkpoint and adapter identity (measured, from the receipts)

| | |
|---|---|
| model path | `/home/grant/decomp/models/qwen2.5-coder-7b` |
| architecture | `Qwen2ForCausalLM`, 28 layers, hidden 3,584, vocab 152,064, bf16 |
| `config.json` sha256 | `c0242402ad6a13b331ea320feea8c7e3776ffb7a4eff0757b9cd667e116d9a28` |
| shards (bytes) | `model-00001-of-00004.safetensors` 4,877,660,776 · `-00002` 4,932,751,008 · `-00003` 4,330,865,200 · `-00004` 1,089,994,880 — **15,231,271,864 B = 14.19 GiB** (`weights_hashed: false`; pass `--hash-weights` to sha256 every shard) |
| tokenizer | `Qwen2Tokenizer`, `eos=<|im_end|>` 151645, `pad=<|endoftext|>` 151643, `model_max_length 32768`, chat template present |
| checkpoint `generation_config.json` | `temperature 0.7, top_p 0.8, top_k 20, repetition_penalty 1.1, do_sample true, eos_token_id [151645, 151643]` — these are the server's defaults, and each receipt records whether a value came from the request or the default |
| adapter `smoke` | `/home/grant/decomp/models/adapters/smoke`, PEFT LoRA r=16 α=32, targets q/k/v/o/gate/up/down_proj, `adapter_model.safetensors` 161,533,192 B, **combined sha256 `4745b84d0cb8f6da9f236f27fc7f8ae318ad8d0a8ea2569ecafdccd37da89694`** |
| adapter effect | **the smoke adapter is effectively an identity** — max abs `lora_A` 0.017, max abs `lora_B` 4.0e-4, so its weight delta is ~2e-4 and greedy output is byte-identical to the base model (§6d) |

---

## 6. Checkpoint behaviour actually measured (not assumed)

`behavior_probe.json` / `.txt`, 2 seeds per condition, `max_tokens 400`, classification done with the
repo's own `solver.llm.classify_extraction` / `extract_c` / `is_refusal`.

| condition | fences | extract_c recovered | refusals | empty |
|---|---|---|---|---|
| chat template, no prefill | 2/2 | 2/2 | 0/2 | 0/2 |
| chat template + `prefill="```c\n"` | 2/2 | 2/2 | 0/2 | 0/2 |
| chat template + prose prefill | 2/2 | 2/2 | 0/2 | 0/2 |
| `/v1/completions`, **no chat template** | 0/2 | 2/2 | 0/2 | 0/2 |
| repair prompt, no prefill | 2/2 | 2/2 | 0/2 | 0/2 |
| repair prompt + prefill | 2/2 | 2/2 | 0/2 | 0/2 |
| repair prompt + bare prefill `"int add"` | 2/2 | 2/2 (1 unterminated) | 0/2 | 0/2 |
| chat template with `add_generation_prompt=false` | 0/2 | 1/2 | 0/2 | **1/2** |

Findings:

1. **This is a base checkpoint, not an instruct tune.** It never refuses (0/12 refusals) — the
   refusal behaviour `solver/llm.py` records belongs to the ollama-served instruct models, not to
   this artifact. Prefill is therefore not needed here to suppress refusals, but it is still the
   mechanism that pins the answer's shape.
2. **It DOES emit fences** — 12/12 fenced answers under the chat template, and `extract_c` recovers
   a function every time. With a prefill, the fence is supplied by the caller and the model
   continues inside it (the returned text starts with the prefill because `echo_prefill` defaults
   true).
3. **Template vs no template is a real behavioural difference**: `/v1/completions` (raw text, no
   template) produced **no fences at all** — the model simply continues the listing. Any experiment
   that mixes the two paths is measuring the template, not the model.
4. **`add_generation_prompt=false` is a trap** (1/2 empty, ≤1 char extracted): without the
   `<|im_start|>assistant\n` header the base model does not answer. This is exactly the failure mode
   `chatfmt.py` exists to prevent.
5. **Rendering equivalence measured**: `render(history) + prefill` is byte-identical to
   `apply_chat_template(history + [assistant(prefill)], continue_final_message=True)`, and both
   differ from the naive full-history rendering (which closes the turn with `<|im_end|>`).
   `test_prefill_is_spliced_into_the_assistant_turn` asserts this; the live server check is in
   `exercise_server.txt` (`continue_final_message renders the same prompt as prefill  PASS`).
6. The default system turn (`You are Qwen, created by Alibaba Cloud…`) is injected by the template
   and is part of every receipt's `rendered_prompt`.

### 6d. LoRA is genuinely applied (and the smoke adapter is a no-op)

`lora_applied_check.txt`. Same prompt, greedy, three model ids on one server:

```
qwen2.5-coder-7b        adapter=None    17 tokens  sha 163b215dc8c33872  "int sub(a,b){return a-b;}"
qwen2.5-coder-7b+smoke  adapter='smoke' 17 tokens  sha 163b215dc8c33872  (identical to base)
qwen2.5-coder-7b+big    adapter='big'   75 tokens  sha 0116d86fd8c7c561  (garbage: 1000x-scaled copy)
LORA_APPLIED
```

`big` is `smoke` with every `lora_A`/`lora_B` tensor multiplied by 1000, built precisely because
`smoke`'s delta (~2e-4) cannot distinguish "adapter applied" from "adapter ignored". The serving
path applies LoRA; **`smoke` itself changes nothing**, so an A/B using it would compare the base
model with itself.

---

## 7. What did not work, and what is a caveat

1. **vLLM 0.29.0 installs but does not run out of the box on WSL2.** Three failures were hit and
   diagnosed in order (`tools/lora_serve/vllm_probe.py`, logs kept at `~/vllm-probe*.log`):
   * `RuntimeError: UVA is not available` — vLLM's V2 GPU model runner allocates a `UvaBuffer`;
     WSL2 does not expose the host-memory-mapping CUDA attribute, so
     `vllm.utils.platform_utils.is_uva_available()` is `False` even though
     `torch.zeros(..., pin_memory=True)` works. Workaround: `VLLM_USE_V2_MODEL_RUNNER=0`.
   * `RuntimeError: Could not find nvcc and default cuda_home='/usr/local/cuda' doesn't exist` —
     FlashInfer JIT-compiles its sampling kernel; there is no system CUDA toolkit in this WSL
     image. Workaround: `VLLM_USE_FLASHINFER_SAMPLER=0` (and `CUDA_HOME` pointed at the toolkit
     wheel inside the venv: `serve-venv/lib/python3.12/site-packages/nvidia/cu13`).
   * `ValueError: No available memory for the cache blocks` at
     `--gpu-memory-utilization 0.55` — fp8 weights need ~7.5 GB, so the KV cache had nothing left.
     `0.72` works on this card once ollama is holding ~2.2 GB.
   With those three settings, vLLM serves this checkpoint and adapter correctly (verified above).
   vLLM was installed into an **isolated** venv `/home/grant/decomp/serve-venv` (torch 2.13.0+cu130,
   8.0 GB); the training venv was not touched.
2. **bf16 does not fit.** The checkpoint is 14.19 GiB of weights; the card is 16 GB with 2.2–3.8 GB
   already held by ollama. Serving is therefore quantised — **vLLM fp8 (on-the-fly at load)** by
   default, **bitsandbytes NF4 4-bit** for the transformers backend. Both arms of any comparison
   must use the same backend and quantisation; the receipt records which. Numbers here were
   measured on fp8 (vLLM) or NF4 (transformers), never on bf16.
3. **Zero free VRAM wedged a request — root cause of the 300 s timeout.** During the first
   transformers concurrency-6 run, `/health` reported `vram_allocated_gb 15.157` and
   `vram_free_gb 0.0`, and small requests queued behind that batch. The receipt log has them:
   a **31-token prompt with `max_tokens 16` took 180.5 s** (05:53) and a **430-token prompt with
   `max_tokens 200` took 390.2 s** (05:56), both started while the 6 × 5.4 k-token batch was in
   flight (`transformers-backend-runs.json`). Cause: the GPU worker held a
   6 × 5.4 k-token batch whose prefill attention is quadratic in prompt length, on top of 5.4 GB of
   4-bit weights, with ollama on the same card — so the small request was not deadlocked, it was
   waiting for a batch that had driven the card to zero free memory. Fixed by bounding a batch two
   ways (`--max-batch-tokens`, `--max-batch-prefill-tokens`) and by
   `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`; the vLLM backend is immune (PagedAttention)
   and is the recommended path. On vLLM the same small requests return in ~85 ms.
4. **`stream: true` is single-chunk SSE**, not incremental tokens: the completion is emitted as one
   delta, then the finish chunk (carrying `usage` and `receipt`), then `[DONE]`. Exact receipts and
   batching were chosen over token-by-token UX.
5. **Stop strings are applied after decoding** (`stop_strings_applied_after_decode: true`), so a
   stopped request reports both `generated_tokens` (what was produced) and `completion_tokens`
   (what was returned). Token-level stopping is not implemented.
6. **Seed determinism** holds for identical batch composition: same prompt + params + seed gives
   byte-identical text across runs and restarts (asserted by `exercise_server.py`). Batched matmul
   reduction order can differ with batch composition, so a near-tie could flip; the receipt records
   the batch the request shared.
7. **One GPU-resident server at a time** (see §1c). Two servers will OOM the card.
8. **Detached background processes**: on this WSL setup a process started with
   `nohup … &`/`setsid` from a `wsl.exe … bash -lc` session is torn down when that session ends.
   `serve.sh start` exists for interactive shells; the reliable launch here is to run
   `serve_vllm.sh` under a harness background job (or any terminal that stays open).
9. **`--default-adapter` defaults to `none` (base).** Registering an adapter never changes what an
   unnamed request gets; an adapter is used only when a request names its model id. `auto` is still
   available opt-in. (`/health` reports `default_adapter: null` and `/v1/models` marks the base id
   as default — see `two_arm_check.json`.)
10. **Not verified**: throughput under `--no-enforce-eager` (CUDA graphs) was not measured;
    `weights_hashed` is false unless `--hash-weights` is passed; the 9,000-token measurement used a
    single prompt shape, so per-prompt variance is unmeasured.

---

## 8. Files in this directory

| file | what it is |
|---|---|
| `sample_request.json`, `sample_response.txt`, `sample_receipt.json` | the real evidence sample: a 12,000-char decompilation-repair prompt, its raw answer, and the complete receipt |
| `throughput.json` | vLLM: concurrency 1 and 6 on the 12,000-char prompt (per-request detail included) |
| `bench-9k-token-prompt/throughput.json` | vLLM: concurrency 1 on the 20,490-char (8,890-token) prompt |
| `transformers-backend-runs.json` | the same measurements on the transformers backend, extracted verbatim from the receipt log (includes the 180 s / 390 s queued requests) |
| `exercise_server.txt` | live PASS/FAIL suite (25 passed, 0 failed) against the served model |
| `behavior_probe.json` / `.txt` | fencing / prefill / template measurements, classified with `solver.llm` |
| `two_arm_check.json` | one process serving base (default) and a named adapter |
| `lora_applied_check.txt` | proof that the serving path applies LoRA (and that `smoke` is a no-op) |

Reproduce with:

```bash
python -m pytest tools/lora_serve/tests/test_lora_serve.py -q          # GPU-free, 29 passed
python -m tools.lora_serve.exercise_server --url http://127.0.0.1:8101
python -m tools.lora_serve.bench --url http://127.0.0.1:8101 \
    --prompt-file .cache/bench_prompt.txt --max-tokens 3000 --concurrency 1 6 \
    --out eval/results/local-inference-20260920
```

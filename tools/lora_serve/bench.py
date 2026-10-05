"""Throughput benchmark + evidence sample for the local inference service.

Two things in one run:

1. **Sample** -- one realistic decompilation-repair request, whose raw response
   and FULL receipt are written to the results directory as evidence.
2. **Throughput** -- the same 12,000-character prompt at concurrency 1 and 6,
   reporting prompt tokens/s, output tokens/s and per-request latency.

    python -m tools.lora_serve.bench --url http://127.0.0.1:8100 \\
        --prompt-file .cache/bench_prompt.txt --max-tokens 1500 \\
        --concurrency 1 6 --out eval/results/local-inference-20260920

Every request asks for an inline receipt, so the numbers in throughput.json can
be re-derived from the receipts alone.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
from pathlib import Path
from typing import Any

from tools.lora_serve.client import InferenceClient, InferenceError


def run_one(client: InferenceClient, prompt: str, index: int, *, model: str | None,
            max_tokens: int, seed: int, temperature: float, top_p: float, top_k: int,
            repetition_penalty: float, prefill: str) -> dict[str, Any]:
    started = time.monotonic()
    result = client.chat([{"role": "user", "content": prompt}], model=model,
                         prefill=prefill, temperature=temperature, top_p=top_p,
                         top_k=top_k, seed=seed + index, max_tokens=max_tokens,
                         repetition_penalty=repetition_penalty)
    wall = time.monotonic() - started
    receipt = result.require_receipt()
    return {
        "index": index,
        "seed": seed + index,
        "client_wall_ms": round(wall * 1000.0, 3),
        "prompt_tokens": receipt["prompt_tokens"],
        "completion_tokens": receipt["completion_tokens"],
        "generated_tokens": receipt["choices"][0]["generated_tokens"],
        "finish_reason": receipt["finish_reason"],
        "stop_reason": receipt["stop_reason"],
        "timings_ms": receipt["timings_ms"],
        "tokens_per_second": receipt["tokens_per_second"],
        "queue_ms": receipt["timings_ms"]["queue_ms"],
        "batch_size": (receipt.get("batch") or {}).get("size"),
        "request_id": receipt["request_id"],
        "raw_response_chars": receipt["raw_response_chars"],
        "adapter_hash": receipt["adapter_hash"],
        "text_head": result.text[:200],
        "text_tail": result.text[-200:],
    }


def benchmark(client: InferenceClient, prompt: str, concurrency: int, *, model: str | None,
              max_tokens: int, seed: int, temperature: float, top_p: float, top_k: int,
              repetition_penalty: float, prefill: str) -> dict[str, Any]:
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(run_one, client, prompt, index, model=model,
                               max_tokens=max_tokens, seed=seed, temperature=temperature,
                               top_p=top_p, top_k=top_k,
                               repetition_penalty=repetition_penalty, prefill=prefill)
                   for index in range(concurrency)]
        rows = [future.result() for future in futures]
    wall = time.monotonic() - started
    prompt_tokens = sum(row["prompt_tokens"] for row in rows)
    completion_tokens = sum(row["completion_tokens"] for row in rows)
    latencies = sorted(row["client_wall_ms"] for row in rows)
    return {
        "concurrency": concurrency,
        "wall_seconds": round(wall, 3),
        "requests": len(rows),
        "prompt_tokens_total": prompt_tokens,
        "completion_tokens_total": completion_tokens,
        "prompt_tokens_per_second": round(prompt_tokens / wall, 2),
        "output_tokens_per_second": round(completion_tokens / wall, 2),
        "total_tokens_per_second": round((prompt_tokens + completion_tokens) / wall, 2),
        "latency_ms": {
            "min": round(latencies[0], 1),
            "median": round(statistics.median(latencies), 1),
            "max": round(latencies[-1], 1),
            "mean": round(statistics.fmean(latencies), 1),
        },
        "per_request": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--model", default=None, help="model id (default: server default)")
    parser.add_argument("--max-tokens", type=int, default=1500)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 6])
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--repetition-penalty", type=float, default=1.1)
    parser.add_argument("--prefill", default="```c\n",
                        help="partial assistant turn (default: an opening C fence)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--skip-sample", action="store_true")
    args = parser.parse_args(argv)

    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = InferenceClient(args.url, timeout=args.timeout)

    health = client.wait_until_ready(timeout=600)
    models = client.models()
    print(f"# server: {json.dumps({k: health[k] for k in health if k != 'backend_report'})}")
    print(f"# models: {[entry['id'] for entry in models]}")
    print(f"# prompt: {len(prompt)} chars, model={args.model or 'default'}")

    report: dict[str, Any] = {
        "url": args.url,
        "prompt_file": str(Path(args.prompt_file).resolve()),
        "prompt_chars": len(prompt),
        "max_tokens": args.max_tokens,
        "sampling": {"temperature": args.temperature, "top_p": args.top_p,
                     "top_k": args.top_k, "seed": args.seed,
                     "repetition_penalty": args.repetition_penalty},
        "prefill": args.prefill,
        "health": health,
        "models": models,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if not args.skip_sample:
        print("# running the evidence sample (1 request)...", flush=True)
        sample_started = time.monotonic()
        result = client.chat([{"role": "user", "content": prompt}], model=args.model,
                             prefill=args.prefill, temperature=args.temperature,
                             top_p=args.top_p, top_k=args.top_k, seed=args.seed,
                             max_tokens=args.max_tokens,
                             repetition_penalty=args.repetition_penalty,
                             receipt_token_ids=False)
        receipt = result.require_receipt()
        (out / "sample_response.txt").write_text(result.text, encoding="utf-8")
        (out / "sample_receipt.json").write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        request_record = {
            "url": args.url, "model": args.model or result.model,
            "messages": [{"role": "user", "content": prompt}],
            "prefill": args.prefill, "temperature": args.temperature,
            "top_p": args.top_p, "top_k": args.top_k, "seed": args.seed,
            "max_tokens": args.max_tokens,
            "repetition_penalty": args.repetition_penalty, "receipt": True,
            "prompt_sha256": receipt["rendered_prompt_sha256"],
        }
        (out / "sample_request.json").write_text(
            json.dumps(request_record, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        report["sample"] = {
            "request_id": result.request_id,
            "client_wall_ms": round((time.monotonic() - sample_started) * 1000.0, 3),
            "prompt_tokens": receipt["prompt_tokens"],
            "completion_tokens": receipt["completion_tokens"],
            "generated_tokens": receipt["choices"][0]["generated_tokens"],
            "finish_reason": receipt["finish_reason"],
            "stop_reason": receipt["stop_reason"],
            "timings_ms": receipt["timings_ms"],
            "tokens_per_second": receipt["tokens_per_second"],
            "rendered_prompt_sha256": receipt["rendered_prompt_sha256"],
            "raw_response_chars": receipt["raw_response_chars"],
            "adapter_hash": receipt["adapter_hash"],
            "model_path": receipt["model"]["path"],
        }
        print(f"# sample: {report['sample']['completion_tokens']} tokens, "
              f"{report['sample']['timings_ms']['decode_ms']:.0f} ms decode, "
              f"{report['sample']['tokens_per_second'] or 0.0:.1f} tok/s", flush=True)

    report["throughput"] = []
    for concurrency in args.concurrency:
        print(f"# benchmarking concurrency {concurrency}...", flush=True)
        try:
            row = benchmark(client, prompt, concurrency, model=args.model,
                            max_tokens=args.max_tokens, seed=args.seed,
                            temperature=args.temperature, top_p=args.top_p,
                            top_k=args.top_k,
                            repetition_penalty=args.repetition_penalty,
                            prefill=args.prefill)
        except InferenceError as exc:
            row = {"concurrency": concurrency, "error": str(exc)}
        report["throughput"].append(row)
        if "error" not in row:
            print(f"#   wall {row['wall_seconds']}s  output {row['output_tokens_per_second']} tok/s"
                  f"  prompt {row['prompt_tokens_per_second']} tok/s"
                  f"  median {row['latency_ms']['median']} ms", flush=True)

    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out / "throughput.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"# wrote {out / 'throughput.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

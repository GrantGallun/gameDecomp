"""Exercise a RUNNING local inference server and print PASS/FAIL lines.

GPU-free (it talks HTTP only), so it can run from any machine that can reach the
service::

    python -m tools.lora_serve.exercise_server --url http://127.0.0.1:8100

Exit code is the number of failures (0 = all passed). It is deliberately not a
pytest file: it needs a live server, and the repo's pytest run must stay
GPU-free and server-free.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from tools.lora_serve.client import InferenceClient, InferenceError

PASS = "PASS"
FAIL = "FAIL"
INFO = "INFO"

_checks: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    _checks.append((status, name, detail))
    print(f"{status}  {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def check(name: str, condition: bool, detail: str = "") -> bool:
    record(PASS if condition else FAIL, name, detail)
    return bool(condition)


REQUIRED_RECEIPT_FIELDS = [
    "schema", "request_id", "model", "adapter", "adapter_hash", "rendered_prompt",
    "rendered_prompt_sha256", "rendered_prompt_tokens", "prefill", "prompt_tokens",
    "completion_tokens", "finish_reason", "stop_reason", "sampling",
    "raw_response_text", "continuation_text", "wall_ms", "timings_ms",
    "tokens_per_second", "server",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    parser.add_argument("--model", default=None,
                        help="model id to exercise (default: whatever the server serves)")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--adapter-model", default=None,
                        help="a second model id (e.g. base or adapter) to exercise too")
    args = parser.parse_args(argv)

    client = InferenceClient(args.url, timeout=args.timeout)
    print(f"# exercising {args.url}\n", flush=True)

    # 1 -- liveness
    try:
        info = client.wait_until_ready(timeout=min(args.timeout, 120))
        check("health endpoint reports ok", info.get("status") == "ok",
              f"uptime={info.get('uptime_s')}s model={info.get('model_path')}")
    except InferenceError as exc:
        record(FAIL, "health endpoint reports ok", str(exc))
        return _summary()

    # 2 -- model identity
    models = client.models()
    ids = [entry["id"] for entry in models]
    check("/v1/models lists at least one model", bool(ids), f"ids={ids}")
    check("/v1/models reports the base model path",
          all(entry["meta"].get("model_path") for entry in models))
    adapters = {entry["meta"].get("adapter"): entry["meta"].get("adapter_hash")
                for entry in models if entry["meta"].get("adapter")}
    check("adapter entries carry a content hash",
          all(h for h in adapters.values()) if adapters else True,
          f"adapters={adapters}")
    base_id = args.model or next((entry["id"] for entry in models
                                  if entry["meta"].get("adapter") is None), ids[0])

    # 3 -- a basic chat completion with a receipt
    result = client.chat([{"role": "user", "content": "Reply with the single word OK."}],
                         model=base_id, temperature=0.0, max_tokens=args.max_tokens,
                         receipt_token_ids=True)
    receipt = result.receipt or {}
    missing = [field for field in REQUIRED_RECEIPT_FIELDS if field not in receipt]
    check("chat completion returns text", bool(result.text.strip()),
          repr(result.text[:60]))
    check("receipt is attached and complete", not missing, f"missing={missing}")
    check("receipt prompt tokens match the token id list",
          not receipt.get("prompt_token_ids")
          or len(receipt["prompt_token_ids"]) == receipt["prompt_tokens"],
          f"prompt_tokens={receipt.get('prompt_tokens')}")
    check("usage totals add up",
          result.usage.get("total_tokens")
          == result.usage.get("prompt_tokens", 0) + result.usage.get("completion_tokens", 0),
          json.dumps(result.usage))
    check("finish reason is stop or length",
          result.finish_reason in ("stop", "length"), result.finish_reason)
    check("wall_ms is positive", (receipt.get("wall_ms") or 0) > 0,
          f"wall_ms={receipt.get('wall_ms')}")

    # 4 -- the receipt is fetchable by request id
    try:
        fetched = client.receipt_for(result.request_id)
        check("GET /receipts/<id> returns the same receipt",
              fetched.get("request_id") == result.request_id
              and fetched.get("raw_response_text") == receipt.get("raw_response_text"))
    except InferenceError as exc:
        record(FAIL, "GET /receipts/<id> returns the same receipt", str(exc))

    # 5 -- prefill lands in the assistant turn and comes back in the text
    prefill = "```c\n"
    prefilled = client.chat(
        [{"role": "user", "content": "Write the C body of int add(int a, int b)."}],
        model=base_id, prefill=prefill, temperature=0.0, max_tokens=96)
    check("prefill is echoed at the head of the response",
          prefilled.text.startswith(prefill), repr(prefilled.text[:40]))
    check("receipt separates prefill from continuation",
          prefilled.receipt.get("prefill") == prefill
          and prefilled.receipt.get("continuation_text") == prefilled.text[len(prefill):])
    check("rendered prompt shows the prefill inside the assistant turn",
          "<|im_start|>assistant\n```c\n" in (prefilled.receipt.get("rendered_prompt") or ""),
          repr((prefilled.receipt.get("rendered_prompt") or "")[-60:]))

    # 6 -- continue_final_message spelling matches the prefill spelling
    via_continue = client.chat(
        [{"role": "user", "content": "Write the C body of int add(int a, int b)."},
         {"role": "assistant", "content": prefill}],
        model=base_id, continue_final_message=True, temperature=0.0, max_tokens=96)
    check("continue_final_message renders the same prompt as prefill",
          via_continue.receipt.get("rendered_prompt")
          == prefilled.receipt.get("rendered_prompt"))

    # 7 -- seeds: reproducible with a seed, greedy stable
    first = client.chat([{"role": "user", "content": "Name a MIPS register."}],
                        model=base_id, temperature=0.9, top_p=0.9, top_k=20,
                        seed=4242, max_tokens=48)
    again = client.chat([{"role": "user", "content": "Name a MIPS register."}],
                        model=base_id, temperature=0.9, top_p=0.9, top_k=20,
                        seed=4242, max_tokens=48)
    check("same seed -> identical continuation",
          first.continuation == again.continuation,
          f"{first.continuation[:30]!r} vs {again.continuation[:30]!r}")
    other = client.chat([{"role": "user", "content": "Name a MIPS register."}],
                        model=base_id, temperature=0.9, top_p=0.9, top_k=20,
                        seed=99, max_tokens=48)
    record(INFO if other.continuation == first.continuation else PASS,
           "different seed -> different continuation",
           "identical text; expected for very short/low-entropy prompts"
           if other.continuation == first.continuation else "")
    greedy_a = client.chat([{"role": "user", "content": "Name a MIPS register."}],
                           model=base_id, temperature=0.0, max_tokens=48)
    greedy_b = client.chat([{"role": "user", "content": "Name a MIPS register."}],
                           model=base_id, temperature=0.0, max_tokens=48)
    check("temperature=0 is deterministic",
          greedy_a.continuation == greedy_b.continuation)

    # 8 -- sampling parameters are recorded as used
    sampling = first.receipt.get("sampling") or {}
    check("receipt records the sampling parameters used",
          sampling.get("temperature") == 0.9 and sampling.get("top_k") == 20
          and sampling.get("seed") == 4242
          and sampling.get("repetition_penalty") is not None,
          json.dumps(sampling))
    check("receipt records where each parameter came from",
          sampling.get("temperature_source") == "request"
          and sampling.get("seed_source") == "request")

    # 9 -- completions endpoint (raw prompt, no template)
    completion = client.complete("def add(a, b):\n", model=base_id, temperature=0.0,
                                 max_tokens=32)
    check("/v1/completions works and is untemplated",
          bool(completion.text) and completion.receipt.get("templated") is False)

    # 10 -- the second model id (base or adapter) also serves, when asked for
    if args.adapter_model:
        other_result = client.chat([{"role": "user", "content": "Reply OK."}],
                                   model=args.adapter_model, temperature=0.0,
                                   max_tokens=24)
        check(f"model {args.adapter_model!r} serves",
              bool(other_result.text),
              f"adapter={other_result.receipt.get('adapter_requested')} "
              f"hash={(other_result.receipt.get('adapter_hash') or '')[:12]}")

    # 11 -- request validation is explicit, not silent
    try:
        client.chat([{"role": "user", "content": "hi"}], extra={"topk": 5})
        record(FAIL, "unknown field is rejected")
    except InferenceError as exc:
        check("unknown field is rejected", exc.status == 400, str(exc)[:80])
    try:
        client.chat([{"role": "user", "content": "hi"}], max_tokens=100000)
        record(FAIL, "over-long request is rejected")
    except InferenceError as exc:
        check("over-long request is rejected", exc.status == 400, str(exc)[:80])

    # 12 -- concurrency: four parallel requests all return receipts
    import concurrent.futures
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(client.chat,
                               [{"role": "user", "content": f"Say the number {index}."}],
                               model=base_id, temperature=0.0, max_tokens=24, seed=index)
                   for index in range(4)]
        outcomes = [future.result() for future in futures]
    check("4 concurrent requests all return receipts",
          all(o.receipt and o.receipt.get("prompt_tokens") for o in outcomes),
          f"{time.monotonic() - started:.1f}s wall")
    batches = {o.receipt.get("batch", {}).get("size") for o in outcomes}
    record(INFO, "batch sizes observed under 4-way concurrency", f"{sorted(batches)}")

    # 13 -- streaming shape (single-chunk SSE)
    streamed = client.chat([{"role": "user", "content": "Reply OK."}], model=base_id,
                           temperature=0.0, max_tokens=16, stream=True)
    check("stream=true returns an OpenAI-shaped SSE stream with a receipt",
          bool(streamed.text) and streamed.receipt is not None)

    return _summary()


def _summary() -> int:
    failures = [name for status, name, _ in _checks if status == FAIL]
    passes = [name for status, name, _ in _checks if status == PASS]
    infos = [name for status, name, _ in _checks if status == INFO]
    print(f"\n{len(passes)} passed, {len(failures)} failed, {len(infos)} info")
    for name in failures:
        print(f"  FAILED: {name}")
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())

"""Verify one process serves both arms: base by default, adapter by model id.

Writes two_arm_check.json into the results directory:

    python -m tools.lora_serve.two_arm_check --url http://127.0.0.1:8101 \\
        --adapter-model qwen2.5-coder-7b+smoke \\
        --out eval/results/local-inference-20260920/two_arm_check.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from tools.lora_serve.client import InferenceClient

PROMPT = ("Write the C body of int sub(int a, int b). Reply with the function only, "
          "inside a single fenced block.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8101")
    parser.add_argument("--adapter-model", required=True,
                        help="model id of the adapter arm, e.g. qwen2.5-coder-7b+smoke")
    parser.add_argument("--base-model", default=None)
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    client = InferenceClient(args.url, timeout=900)
    health = client.wait_until_ready(timeout=300)
    models = [entry["id"] for entry in client.models()]
    base_id = args.base_model or next(
        (entry["id"] for entry in client.models() if entry["meta"].get("adapter") is None),
        models[0])

    def run(model: str | None) -> dict:
        started = time.monotonic()
        result = client.chat([{"role": "user", "content": PROMPT}], model=model,
                             prefill="```c\n", temperature=0.0, max_tokens=args.max_tokens)
        receipt = result.require_receipt()
        return {
            "requested_model": model,
            "resolved_model_id": receipt["model_id"],
            "adapter_requested": receipt["adapter_requested"],
            "adapter_hash": receipt["adapter_hash"],
            "text": result.text,
            "raw_response_sha256": receipt["raw_response_sha256"],
            "prompt_tokens": receipt["prompt_tokens"],
            "completion_tokens": receipt["completion_tokens"],
            "wall_ms": receipt["wall_ms"],
            "client_wall_ms": round((time.monotonic() - started) * 1000.0, 1),
            "request_id": result.request_id,
        }

    unnamed = run(None)
    named = run(args.adapter_model)
    payload = {
        "url": args.url,
        "health_default_adapter": health.get("default_adapter"),
        "health_adapters": health.get("adapters"),
        "models": [entry["id"] for entry in client.models()],
        "base_id": base_id,
        "unnamed_request": unnamed,
        "named_request": named,
        "outputs_identical": unnamed["raw_response_sha256"] == named["raw_response_sha256"],
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"health.default_adapter = {payload['health_default_adapter']}")
    print(f"models                 = {payload['models']}")
    print(f"unnamed request  -> adapter_requested={unnamed['adapter_requested']!r} "
          f"hash={(unnamed['adapter_hash'] or 'none')[:16]}")
    print(f"named request    -> adapter_requested={named['adapter_requested']!r} "
          f"hash={(named['adapter_hash'] or 'none')[:16]}")
    print(f"outputs identical = {payload['outputs_identical']} "
          f"(expected False for a real LoRA; the smoke adapter is known-degenerate)")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

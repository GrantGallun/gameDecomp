"""Attempt to serve THIS checkpoint with vLLM and record exactly what happens.

One configuration per process, so a CUDA OOM or a kernel failure cannot take the
whole probe down::

    /home/grant/decomp/serve-venv/bin/python -m tools.lora_serve.vllm_probe \\
        --quantization fp8 --lora --gpu-memory-utilization 0.6 --max-model-len 8192

Prints a single JSON object on success (and writes it to --out), or a JSON object
with ``"ok": false`` plus the captured error on failure. The results README
quotes these runs; nothing here is inferred from documentation.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from pathlib import Path

MODEL = "/home/grant/decomp/models/qwen2.5-coder-7b"
ADAPTER = "/home/grant/decomp/models/adapters/smoke"


def vram() -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        import torch
        free, total = torch.cuda.mem_get_info()
        out["free_gb"] = round(free / 2**30, 3)
        out["total_gb"] = round(total / 2**30, 3)
        out["allocated_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
        out["peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--adapter", default=ADAPTER)
    parser.add_argument("--quantization", default="none",
                        choices=["none", "fp8", "bitsandbytes"])
    parser.add_argument("--lora", action="store_true")
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.6)
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--max-num-seqs", type=int, default=6)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--enforce-eager", action="store_true", default=True)
    parser.add_argument("--no-enforce-eager", dest="enforce_eager", action="store_false")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    result: dict = {
        "config": {
            "model": args.model, "adapter": args.adapter if args.lora else None,
            "quantization": args.quantization, "lora": args.lora,
            "gpu_memory_utilization": args.gpu_memory_utilization,
            "max_model_len": args.max_model_len, "max_num_seqs": args.max_num_seqs,
            "enforce_eager": args.enforce_eager,
        },
        "ok": False,
        "vram_before": vram(),
    }
    started = time.monotonic()
    try:
        from vllm import LLM, SamplingParams
        import vllm

        result["vllm_version"] = vllm.__version__
        kwargs: dict = {
            "model": args.model,
            "gpu_memory_utilization": args.gpu_memory_utilization,
            "max_model_len": args.max_model_len,
            "max_num_seqs": args.max_num_seqs,
            "enforce_eager": args.enforce_eager,
            "disable_log_stats": True,
            "trust_remote_code": False,
        }
        if args.quantization != "none":
            kwargs["quantization"] = args.quantization
        if args.lora:
            kwargs.update({"enable_lora": True, "max_lora_rank": 16, "max_loras": 1})
        load_started = time.monotonic()
        llm = LLM(**kwargs)
        result["load_seconds"] = round(time.monotonic() - load_started, 3)
        result["vram_after_load"] = vram()

        tokenizer = llm.get_tokenizer()
        prompt = tokenizer.apply_chat_template(
            [{"role": "user", "content": "Write the C function int add(int a, int b)."}],
            tokenize=False, add_generation_prompt=True) + "```c\n"
        token_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        sampling = SamplingParams(temperature=0.0, max_tokens=args.max_tokens, top_k=20,
                                  top_p=0.8, repetition_penalty=1.1)

        gen_started = time.monotonic()
        outputs = llm.generate([{"prompt_token_ids": token_ids}], sampling)
        result["base_generate_seconds"] = round(time.monotonic() - gen_started, 3)
        output = outputs[0]
        result["base"] = {
            "prompt_tokens": len(output.prompt_token_ids or token_ids),
            "completion_tokens": len(output.outputs[0].token_ids),
            "finish_reason": output.outputs[0].finish_reason,
            "text_head": output.outputs[0].text[:120],
            "metrics": {
                key: value for key, value in vars(output.metrics).items()
                if isinstance(value, (int, float, str, type(None)))
            } if getattr(output, "metrics", None) else None,
        }
        if args.lora:
            from vllm.lora.request import LoRARequest
            lora_started = time.monotonic()
            lora_outputs = llm.generate(
                [{"prompt_token_ids": token_ids}],
                SamplingParams(temperature=0.0, max_tokens=args.max_tokens, top_k=20,
                               top_p=0.8, repetition_penalty=1.1),
                lora_request=LoRARequest("smoke", 1, args.adapter))
            result["lora_generate_seconds"] = round(time.monotonic() - lora_started, 3)
            result["lora"] = {
                "completion_tokens": len(lora_outputs[0].outputs[0].token_ids),
                "finish_reason": lora_outputs[0].outputs[0].finish_reason,
                "text_head": lora_outputs[0].outputs[0].text[:120],
                "differs_from_base":
                    lora_outputs[0].outputs[0].text != output.outputs[0].text,
            }
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - the failure IS the measurement
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback_tail"] = traceback.format_exc().splitlines()[-25:]
    result["vram_after"] = vram()
    result["wall_seconds"] = round(time.monotonic() - started, 3)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

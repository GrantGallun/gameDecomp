"""Measure how THIS checkpoint actually behaves behind the service.

The wider project has mis-modelled reasoning-model output channels and prefill
behaviour twice (``solver/llm.py``), so the service ships the probe that would
have caught it: the same prompt is sent with and without a chat template, with
and without a partial assistant turn, and every response is classified with the
repo's own extractor.

    python -m tools.lora_serve.behavior_probe --url http://127.0.0.1:8100 \\
        --out eval/results/local-inference-20260920/behavior_probe.json

Classification reuses ``solver.llm`` (read-only import) so the labels mean what
they mean everywhere else in the repo: refusal / empty / fence / asm /
unterminated / ok, plus whether ``extract_c`` recovers a function.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from tools.lora_serve.client import InferenceClient

CODE_REQUEST = ("Write the body of the C function `int add(int a, int b)` that returns "
                "a + b. Use the exact signature. Keep it to the function only.")

RAW_PROMPT = ("/* N64 decompilation: add two signed ints, IDO 5.3 -O2 */\n"
              "int add(int a, int b) {\n")

REPAIR_REQUEST = (
    "Repair this decompiled C so it compiles byte-for-byte to the target MIPS "
    "assembly (IDO 5.3, -O2).\n\nTarget assembly:\n"
    "/* 0000 27BDFFF8 */ addiu  sp, sp, -8\n"
    "/* 0004 AFBF0004 */ sw     ra, 4(sp)\n"
    "/* 0008 00851021 */ addu   v0, a0, a1\n"
    "/* 000C 8FBF0004 */ lw     ra, 4(sp)\n"
    "/* 0010 03E00008 */ jr     ra\n"
    "/* 0014 27BD0008 */ addiu  sp, sp, 8\n\n"
    "Current attempt compiles to a different schedule; return the corrected "
    "function in a single ```c block.")


def classify(text: str, prefill: str, client_receipt: dict[str, Any]) -> dict[str, Any]:
    from solver.llm import classify_extraction, extract_c, is_refusal
    extracted = extract_c(text)
    continuation = text[len(prefill):] if prefill and text.startswith(prefill) else text
    return {
        "chars": len(text),
        "continuation_chars": len(continuation),
        "has_fence": "```" in text,
        "starts_with_fence": continuation.lstrip().startswith("```") or bool(prefill),
        "echoes_prefill": bool(prefill) and text.startswith(prefill),
        "classify_extraction": classify_extraction(text, extracted),
        "extract_c_chars": len(extracted),
        "extract_c_has_function": bool(extracted) and "{" in extracted,
        "is_refusal": is_refusal(text),
        "finish_reason": client_receipt.get("finish_reason"),
        "completion_tokens": client_receipt.get("completion_tokens"),
        "continuation_head": continuation[:160],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    parser.add_argument("--model", default=None)
    parser.add_argument("--no-adapter-model", default=None,
                        help="second model id to probe (e.g. the base model id)")
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 12])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    client = InferenceClient(args.url, timeout=900)
    health = client.wait_until_ready(timeout=300)
    models = [entry["id"] for entry in client.models()]
    model_id = args.model or next(
        (entry["id"] for entry in client.models() if entry["meta"].get("adapter") is None),
        models[0])

    trials: list[dict[str, Any]] = []

    def run(name: str, kind: str, *, prompt: str | None, messages: Any,
            prefill: str, model: str, add_generation_prompt: bool = True) -> None:
        for seed in args.seeds:
            started = time.monotonic()
            kwargs: dict[str, Any] = {"model": model, "temperature": 0.2, "top_p": 0.8,
                                      "top_k": 20, "seed": seed,
                                      "max_tokens": args.max_tokens}
            if kind == "chat":
                if prefill:
                    kwargs["prefill"] = prefill
                if not add_generation_prompt:
                    kwargs["extra"] = {"add_generation_prompt": False}
                result = client.chat(messages, **kwargs)
            else:
                result = client.complete(prompt or "", **kwargs)
            receipt = result.require_receipt()
            trials.append({
                "probe": name, "endpoint": kind, "model": model, "seed": seed,
                "prefill": prefill,
                "add_generation_prompt": add_generation_prompt,
                "prompt_tokens": receipt["prompt_tokens"],
                "rendered_prompt": receipt["rendered_prompt"],
                "raw_response_text": receipt["raw_response_text"],
                "continuation_text": receipt["continuation_text"],
                "client_wall_ms": round((time.monotonic() - started) * 1000.0, 1),
                "classification": classify(receipt["raw_response_text"], prefill, receipt),
            })
            print(f"  {name:26s} seed={seed:<4} -> "
                  f"{trials[-1]['classification']['classify_extraction']:12s} "
                  f"fence={trials[-1]['classification']['has_fence']} "
                  f"extract={trials[-1]['classification']['extract_c_chars']}c", flush=True)

    print(f"# probing {args.url} model={model_id}")
    run("chat_template", "chat", prompt=None, messages=[{"role": "user", "content": CODE_REQUEST}],
        prefill="", model=model_id)
    run("chat_template+prefill", "chat", prompt=None,
        messages=[{"role": "user", "content": CODE_REQUEST}], prefill="```c\n",
        model=model_id)
    run("chat_template+prefill_prose", "chat", prompt=None,
        messages=[{"role": "user", "content": CODE_REQUEST}],
        prefill="Here is the function:\n\n```c\n", model=model_id)
    run("raw_completion_no_template", "completion", prompt=RAW_PROMPT, messages=None,
        prefill="", model=model_id)
    run("repair_no_prefill", "chat", prompt=None,
        messages=[{"role": "user", "content": REPAIR_REQUEST}], prefill="", model=model_id)
    run("repair+prefill", "chat", prompt=None,
        messages=[{"role": "user", "content": REPAIR_REQUEST}], prefill="```c\n",
        model=model_id)
    run("repair+prefill_assistant_only", "chat", prompt=None,
        messages=[{"role": "user", "content": REPAIR_REQUEST}], prefill="int add",
        model=model_id)
    run("no_generation_prompt", "chat", prompt=None,
        messages=[{"role": "user", "content": CODE_REQUEST}], prefill="",
        model=model_id, add_generation_prompt=False)

    if args.no_adapter_model:
        run("adapter_chat_template", "chat", prompt=None,
            messages=[{"role": "user", "content": CODE_REQUEST}], prefill="",
            model=args.no_adapter_model)
        run("adapter+prefill", "chat", prompt=None,
            messages=[{"role": "user", "content": CODE_REQUEST}], prefill="```c\n",
            model=args.no_adapter_model)

    summary: dict[str, Any] = {}
    for trial in trials:
        key = trial["probe"]
        entry = summary.setdefault(key, {"n": 0, "fence": 0, "refusal": 0, "empty": 0,
                                         "extracted": 0, "echoes_prefill": 0,
                                         "classifications": []})
        entry["n"] += 1
        entry["fence"] += int(trial["classification"]["has_fence"])
        entry["refusal"] += int(trial["classification"]["is_refusal"])
        entry["empty"] += int(not trial["continuation_text"].strip())
        entry["extracted"] += int(bool(trial["classification"]["extract_c_chars"]))
        entry["echoes_prefill"] += int(trial["classification"]["echoes_prefill"])
        entry["classifications"].append(trial["classification"]["classify_extraction"])

    payload = {
        "url": args.url,
        "model_id": model_id,
        "health": {k: health[k] for k in health if k != "backend_report"},
        "max_tokens": args.max_tokens,
        "seeds": args.seeds,
        "summary": summary,
        "trials": trials,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\n# wrote {out}")
    for key, entry in summary.items():
        print(f"  {key:28s} fences {entry['fence']}/{entry['n']}  "
              f"extracted {entry['extracted']}/{entry['n']}  "
              f"refusals {entry['refusal']}/{entry['n']}  "
              f"empty {entry['empty']}/{entry['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

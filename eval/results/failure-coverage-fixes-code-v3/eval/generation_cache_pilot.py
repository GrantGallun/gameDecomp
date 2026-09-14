"""Mechanical S2 check for explicit-seed generation caching.

The first seed is requested twice: the second request must be a zero-generation
cache hit returning byte-identical text.  A different seed must use a distinct
key and generate a new draw.  This checks cache semantics, not model quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from solver import llm


PROMPT = """Return exactly one fenced C block containing this statement:
int cache_probe = 1;
No prose."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--think", default="low")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--num-thread", type=int, default=8)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int,
                        help="base seed; defaults to a fresh time-derived seed")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else int(time.time_ns() % 2**31)
    endpoint = llm.host()
    calls = []
    for label, draw_seed in (("first", seed), ("replay", seed),
                             ("new_draw", seed + 1)):
        started = time.perf_counter()
        text, meta = llm.generate(
            endpoint, args.model, PROMPT, timeout=args.timeout,
            num_thread=args.num_thread, num_predict=256, think=args.think,
            temperature=0.5, seed=draw_seed,
            cache_dir=args.cache_dir, cache_namespace="s2-mechanical-v1")
        cache_hit = bool(meta.get("_cache_hit"))
        tokens = int(meta.get("eval_count", 0) or 0)
        calls.append({
            "label": label,
            "seed": draw_seed,
            "cache_hit": cache_hit,
            "cache_key": meta.get("_cache_key"),
            "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "response_chars": len(text),
            "recorded_generation_tokens": tokens,
            "charged_generation_tokens": 0 if cache_hit else tokens,
            "avoided_generation_tokens": tokens if cache_hit else 0,
            "wall_seconds": round(time.perf_counter() - started, 4),
            "done_reason": meta.get("done_reason", ""),
        })

    first, replay, new_draw = calls
    checks = {
        "replay_was_cache_hit": replay["cache_hit"],
        "replay_key_identical": replay["cache_key"] == first["cache_key"],
        "replay_text_identical": (
            replay["response_sha256"] == first["response_sha256"]),
        "new_seed_key_distinct": (
            new_draw["cache_key"] != first["cache_key"]),
        "new_seed_generated": not new_draw["cache_hit"],
    }
    receipt = {
        "schema_version": 1,
        "kind": "generation_cache_mechanical_pilot",
        "model": args.model,
        "think": args.think,
        "temperature": 0.5,
        "num_predict": 256,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "cache_dir": str(args.cache_dir),
        "calls": calls,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
        "created_at": int(time.time()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps(receipt, indent=2), flush=True)
    if receipt["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

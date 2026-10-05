"""Training files for the three trained arms of the logic pilot (LOGIC_PILOT.md), with equal example counts.

    python3 logic_arms.py TASKS.jsonl --out DIR [--cap 2400]   -> DIR/{repair,logic,mixed}.jsonl + arms.json

repair = logic-explain (residual -> edit); logic = logic-predict + logic-need (incl. twins and withheld-fact tasks);
mixed = half of each, interleaved. Every arm gets the same N train examples, N = min(cap, |repair|, |logic|), in the
exporter's hash order, so the arms differ in WHAT they train on, not how much. Exam/check rows are not copied: the
trainer only reads the train split.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

def build_arms(tasks, tokenizer, *, cap=2400, max_seq_len=3072, grad_accum=4):
    from eval.repair_prompts import load_task_examples
    from eval.train_source_repair import render

    train = [t for t in tasks if t["split"] == "train"]
    lengths = {}
    for example in load_task_examples(train, split="train"):
        ids, boundary = render(tokenizer, example)
        lengths[example.record_id] = {"tokens": len(ids), "completion_tokens": len(ids) - boundary}
    dropped = sum(lengths[t["id"]]["tokens"] > max_seq_len for t in train)
    train = [t for t in train if lengths[t["id"]]["tokens"] <= max_seq_len]
    repair = [t for t in train if t["kind"] == "logic-explain"]
    logic = [t for t in train if t["kind"] in ("logic-predict", "logic-need")]
    n = min(cap, len(repair), len(logic))
    n -= n % grad_accum
    if not n:
        raise ValueError("not enough length-admitted examples for one full optimizer step per arm")
    half = n // 2
    mixed = [t for pair in zip(repair[:half], logic[:half]) for t in pair] + logic[half:n - half]
    arms = {"repair": repair[:n], "logic": logic[:n], "mixed": mixed}
    assert all(len(rows) == n for rows in arms.values()), {k: len(v) for k, v in arms.items()}
    return arms, {"n": n, "max_seq_len": max_seq_len, "grad_accum": grad_accum,
                  "dropped_for_length": dropped, "available": {"repair": len(repair), "logic": len(logic)},
                  "token_budgets": {name: {key: sum(lengths[t["id"]][key] for t in rows)
                                          for key in ("tokens", "completion_tokens")} for name, rows in arms.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--cap", type=int, default=2400)
    ap.add_argument("--max-seq-len", type=int, default=3072)
    a = ap.parse_args()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(a.base), local_files_only=True)
    data = a.tasks.read_bytes()
    tasks = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    arms, receipt = build_arms(tasks, tokenizer, cap=a.cap, max_seq_len=a.max_seq_len)
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out} is not empty: refusing to overwrite frozen training arms")
    a.out.mkdir(parents=True, exist_ok=True)
    receipt.update({"tasks": str(a.tasks), "tasks_sha256": hashlib.sha256(data).hexdigest(),
                    "base": str(a.base), "arms": {}})
    for name, rows in arms.items():
        data = "".join(json.dumps(r) + "\n" for r in rows).encode()
        (a.out / f"{name}.jsonl").write_bytes(data)
        kinds = {}
        for r in rows:
            k = r["kind"] + (":" + r["variant"].split(":")[0] if r.get("variant", "base") != "base" else "")
            kinds[k] = kinds.get(k, 0) + 1
        receipt["arms"][name] = {"examples": len(rows), "sha256": hashlib.sha256(data).hexdigest(), "kinds": kinds}
    (a.out / "arms.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt, indent=1))


if __name__ == "__main__":
    main()

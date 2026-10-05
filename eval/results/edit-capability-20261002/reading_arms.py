"""Training files for the reading pilot: the logic pilot's `mixed` arm plus R extra examples, two ways.

    python3 reading_arms.py --mixed MIXED.jsonl --logic LOGIC_TASKS.jsonl --reading READING_TASKS.jsonl \
                            --out DIR --base MODEL [--extra 1948]   -> DIR/{read,more}.jsonl + arms.json

read = mixed + R/2 logic-read + R/2 reading drop_stmt explain tasks (reading_tasks.py)
more = mixed + R/2 further logic-explain + R/2 further logic-predict/need tasks from the SAME logic export, none
       already in mixed
Both arms get the same number of examples, so a difference is WHAT was added, not how much (the control the
tool-learning and logic pilots also use). Train split only; same 3072-token admission as logic_arms.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def order(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda t: hashlib.sha256(f"reading-arms:{t['id']}".encode()).hexdigest())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mixed", type=Path, required=True)
    ap.add_argument("--logic", type=Path, required=True)
    ap.add_argument("--reading", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--extra", type=int, default=1948)
    ap.add_argument("--max-seq-len", type=int, default=3072)
    a = ap.parse_args()
    from transformers import AutoTokenizer
    from eval.repair_prompts import load_task_examples
    from eval.train_source_repair import render
    tok = AutoTokenizer.from_pretrained(str(a.base), local_files_only=True)

    def fits(rows):
        ex = {e.record_id: e for e in load_task_examples(rows, split="train")}
        return [t for t in rows if t["id"] in ex and len(render(tok, ex[t["id"]])[0]) <= a.max_seq_len]

    mixed = load(a.mixed)
    used = {t["id"] for t in mixed}
    logic = [t for t in load(a.logic) if t["split"] == "train" and t["id"] not in used]
    reading = [t for t in load(a.reading) if t["split"] == "train"]
    half = a.extra // 2
    pools = {
        "read": (fits(order([t for t in reading if t["kind"] == "logic-read"])),
                 fits(order([t for t in reading if t["kind"] == "logic-explain"]))),
        "more": (fits(order([t for t in logic if t["kind"] == "logic-explain"])),
                 fits(order([t for t in logic if t["kind"] in ("logic-predict", "logic-need")]))),
    }
    n_half = min(half, *(len(p) for pair in pools.values() for p in pair))
    if n_half < half:
        print(f"note: only {n_half} per half admitted (asked {half})", file=sys.stderr)
    if a.out.exists() and any(a.out.iterdir()):
        raise SystemExit(f"{a.out} is not empty: refusing to overwrite frozen training arms")
    a.out.mkdir(parents=True, exist_ok=True)
    receipt = {"mixed": str(a.mixed), "mixed_sha256": hashlib.sha256(a.mixed.read_bytes()).hexdigest(),
               "logic": str(a.logic), "reading": str(a.reading),
               "reading_sha256": hashlib.sha256(a.reading.read_bytes()).hexdigest(),
               "extra_per_half": n_half, "max_seq_len": a.max_seq_len, "arms": {}}
    for name, (first, second) in pools.items():
        extra = [t for pair in zip(first[:n_half], second[:n_half]) for t in pair]
        # Interleave the extra examples through mixed so neither block is trained last.
        rows, k = [], 0
        for i, t in enumerate(mixed):
            rows.append(t)
            if k < len(extra):
                rows.append(extra[k])
                k += 1
        rows += extra[k:]
        rows = rows[:len(rows) - len(rows) % 4]
        data = "".join(json.dumps(r) + "\n" for r in rows).encode()
        (a.out / f"{name}.jsonl").write_bytes(data)
        kinds = {}
        for r in rows:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        receipt["arms"][name] = {"examples": len(rows), "sha256": hashlib.sha256(data).hexdigest(), "kinds": kinds}
    (a.out / "arms.json").write_text(json.dumps(receipt, indent=1))
    print(json.dumps(receipt, indent=1))


if __name__ == "__main__":
    main()

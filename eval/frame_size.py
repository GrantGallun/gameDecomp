"""IDO 5.3 frame size vs a formula over calls, saved registers and memory-resident locals (no compiles).

Protocol: eval/results/frame-size-20260923/PROTOCOL.md.

    python -m eval.frame_size TRACE_DIR --repo ~/decomp/sbk1 --out DIR
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

from eval.allocator_rules import procedures

FRAME = re.compile(r"^addiu\s+sp,sp,-(0x[0-9a-f]+|\d+)", re.M)
SAVE_INT = re.compile(r"^sw\s+(s[0-8]|ra|fp),(?:0x[0-9a-f]+|\d+)\(sp\)", re.M)
SAVE_FLT = re.compile(r"^(?:sdc1|swc1)\s+\$?(f\d+),(?:0x[0-9a-f]+|\d+)\(sp\)", re.M)


def align8(n: int) -> int:
    return (n + 7) & ~7


def facts(dump: str, proc) -> dict:
    lines = [l.strip() for l in dump.splitlines() if l.strip()]
    text = "\n".join(lines)
    m = FRAME.search("\n".join(lines[:3]))
    frame = int(m.group(1), 0) if m else 0
    prologue = "\n".join(lines[:24])
    saved_int = len(set(SAVE_INT.findall(prologue)))
    saved_float = len(set(SAVE_FLT.findall(prologue)))
    calls = bool(re.search(r"^(jal|jalr)\b", text, re.M))
    by_node = collections.defaultdict(list)
    for r in proc.ranges.values():
        if r.kind == "M":
            by_node[r.node].append(r.color)
    memory_locals = sum(1 for colours in by_node.values() if all(c <= 0 for c in colours))
    predicted = align8((16 if calls else 0) + 4 * saved_int + 8 * saved_float + 4 * memory_locals)
    # AMENDMENT (after residual_check.py: every residual >= +16 wrote a stack argument slot): the outgoing area
    # is max(16, highest stack-argument slot written below the saved-register area + 4).
    floor = frame - 4 * saved_int - 8 * saved_float
    slots = [int(m.group(1), 0) for m in re.finditer(r"^s[bhw]c?1?\s+\$?\w+,(0x[0-9a-f]+|\d+)\(sp\)", text, re.M)]
    outgoing = max([16] + [s + 4 for s in slots if 16 <= s < floor]) if calls else 0
    amended = align8(outgoing + 4 * saved_int + 8 * saved_float + 4 * memory_locals)
    return {"frame": frame, "predicted": predicted, "amended": amended, "calls": calls, "saved_int": saved_int,
            "saved_float": saved_float, "memory_locals": memory_locals}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("traces", type=Path)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    rows = []
    for proc in procedures(args.traces):
        dump = args.repo / "nonmatchings" / proc.name / "target_object_dump_normalized.s"
        if dump.exists():
            rows.append({"function": proc.name, **facts(dump.read_text(errors="replace"), proc)})
    hit = sum(r["frame"] == r["predicted"] for r in rows)
    residual = collections.Counter(r["frame"] - r["predicted"] for r in rows)
    by_shape = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        key = ("calls" if r["calls"] else "leaf", r["memory_locals"] > 0)
        by_shape[key][0] += 1
        by_shape[key][1] += r["frame"] == r["predicted"]
    amended_hit = sum(r["frame"] == r["amended"] for r in rows)
    amended_residual = collections.Counter(r["frame"] - r["amended"] for r in rows)
    result = {"functions": len(rows), "exact": hit, "rate": round(hit / max(1, len(rows)), 4),
              "amendment_outgoing_area": {"exact": amended_hit, "rate": round(amended_hit / max(1, len(rows)), 4),
                                          "residual": dict(amended_residual.most_common(10))},
              "verdict": "confirmed" if rows and hit / len(rows) >= 0.90 else "not confirmed",
              "residual_target_minus_predicted": dict(residual.most_common(12)),
              "by_shape": {f"{k[0]}/memory_locals={k[1]}": {"n": v[0], "exact": v[1]} for k, v in by_shape.items()},
              "misses": [r for r in rows if r["frame"] != r["predicted"]][:25]}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "misses"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

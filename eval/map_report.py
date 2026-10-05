"""Read a dumped /api/map JSON and report the distributions the treemap draws from.

Written because the shell quoting needed to grep JSON from PowerShell mangled every attempt, and
because "the map moved" is a claim that needs a number behind it: area is target bytes and colour is
campaign state, so the two things worth counting are total bytes and the status histogram.

    python3 -m eval.map_report /tmp/map.json [--compare /tmp/map-before.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


EXACT_STATUSES = {"exact", "object_exact", "function_exact_pending_integration", "integrated"}
BYTE_KEYS = ("total_bytes", "exact_bytes")


def histogram(payload: dict) -> dict:
    functions = payload.get("functions") or []
    status = collections.Counter(f.get("status") for f in functions)
    category = collections.Counter(f.get("category") for f in functions)
    sized = [f for f in functions if isinstance(f.get("size"), int) and f["size"]]
    # The map's own vocabulary, not `eval/status.py`'s. A campaign checkpoint calls a function
    # `object_exact`; `status.py` calls a function byte-exact when an attempt is MATCHED into the
    # build. The two numbers answer different questions and must not be quoted interchangeably --
    # 937 object_exact against 333 byte-exact is the size of that gap.
    exact = [f for f in sized if f.get("status") in EXACT_STATUSES]
    out = {"functions": len(functions),
           "sized_functions": len(sized),
           "target_bytes": sum(f["size"] for f in sized),
           "object_exact_functions": len(exact),
           "object_exact_bytes": sum(f["size"] for f in exact),
           "status": dict(status),
           "category": dict(category),
           "map_totals": {k: payload[k] for k in BYTE_KEYS if k in payload},
           "keys": sorted(payload)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("map_json", type=Path)
    ap.add_argument("--compare", type=Path, default=None,
                    help="an earlier map dump; report what changed")
    args = ap.parse_args(argv)

    now = histogram(load(args.map_json))
    print(json.dumps({k: v for k, v in now.items() if k not in {"status", "category"}}, indent=2))
    print("status  :", json.dumps(now["status"], sort_keys=True))
    print("category:", json.dumps(now["category"], sort_keys=True))

    if args.compare:
        before = histogram(load(args.compare))
        print("\n--- change ---")
        for key in ("functions", "sized_functions", "target_bytes", "object_exact_functions",
                    "object_exact_bytes"):
            delta = now[key] - before[key]
            print(f"{key:<18} {before[key]:>8} -> {now[key]:>8}  ({delta:+d})")
        for status in sorted(set(before["status"]) | set(now["status"])):
            was, is_ = before["status"].get(status, 0), now["status"].get(status, 0)
            if was != is_:
                print(f"  status {status:<16} {was:>6} -> {is_:>6}  ({is_ - was:+d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

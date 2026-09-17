"""Every ledger under eval/results that claims object-exact functions, and whether we count them.

Round 1 found 22 verified matches sitting in `failure-coverage-fresh-paired-*` ledgers that no count
could see. That glob covered one family. This looks at ALL of them, because the lesson is that a
ledger nobody reads is not a record -- and the cheapest verified gain available is finding more of them.

Reports per file: how many entries claim object-exact, and of those how many the knowledge base already
counts. Only the uncounted ones are candidates, and none is believed until it recompiles.

    python3 eval/ledger_inventory.py [--out FILE]
"""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
EXACT = ("object_exact", "exact", "function_exact_pending_integration", "integrated")
# Frozen code snapshots and staged copies are historical receipts, not live ledgers; counting their
# rows would be reading somebody's old experiment as current state.
SKIP = ("/staged", "-code-v", "/code/", "/release/", "staged-", "-artifacts/")


def entries(data: dict):
    for key in ("nodes", "rows", "work_items"):
        value = data.get(key)
        if isinstance(value, dict):
            for name, node in value.items():
                if isinstance(node, dict):
                    yield str(name), node
                elif isinstance(node, str) and node in EXACT:
                    yield str(name), {"state": node}
        elif isinstance(value, list):
            for node in value:
                if isinstance(node, dict):
                    yield str(node.get("function") or node.get("name") or "?"), node
    for key in ("exact_functions", "reproduced_exact"):
        value = data.get(key)
        if isinstance(value, list):
            for name in value:
                yield str(name), {"state": "object_exact"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, default=ROOT / "eval/results")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB))
    known_exact: set[str] = set()
    for (name,) in conn.execute(
            "select distinct f.name from functions f join attempts a on a.func_addr = f.addr "
            "where a.exact = 1"):
        known_exact.add(name)
    in_kb = {r[0] for r in conn.execute("select name from functions")}

    rows = []
    # TWO LEVELS, not recursive. `eval/results` contains frozen copies of the entire tree, so a
    # recursive scan loads thousands of unrelated JSON files and was killed before producing anything.
    # Every live ledger sits at results/<name>.json or results/<run>/<name>.json.
    candidates = sorted(glob.glob(str(args.results / "*.json"))) + \
        sorted(glob.glob(str(args.results / "*" / "*.json")))
    for path in candidates:
        if any(marker in path for marker in SKIP):
            continue
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        claimed = {}
        for name, node in entries(data):
            state = node.get("state") or node.get("status")
            if node.get("object_exact") or state in EXACT or node.get("exact"):
                claimed[name] = node
        if not claimed:
            continue
        rel = path.replace(str(args.results) + "/", "")
        uncounted = [n for n in claimed if n in in_kb and n not in known_exact]
        rows.append({"ledger": rel, "claimed": len(claimed), "uncounted": sorted(uncounted),
                     "not_in_kb": sorted(n for n in claimed if n not in in_kb)})

    rows.sort(key=lambda r: -len(r["uncounted"]))
    print(f"{'ledger':<66} {'claims':>7} {'uncounted':>10}")
    for row in rows[:25]:
        print(f"{row['ledger']:<66} {row['claimed']:>7} {len(row['uncounted']):>10}")
    total = {name for row in rows for name in row["uncounted"]}
    print(f"\nledgers with object-exact claims: {len(rows)}")
    print(f"distinct uncounted functions across them: {len(total)}")
    print(Counter(name for row in rows for name in row["uncounted"]).most_common(5))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"ledgers": rows, "uncounted": sorted(total)}, indent=2),
                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

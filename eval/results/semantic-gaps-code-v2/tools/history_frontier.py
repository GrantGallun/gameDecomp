"""Rank unmatched target functions with exact source in SBK1 git history.

Semantic renames hide the connection: ``getRacePlayerPathOffset`` is still the
ROM function at 0x8007BDE4, while the historical commit calls it
``func_8007BDE4``.  The function address is the stable join key.  This report
turns that join into an explicit work queue without treating history as proof;
every recovered source must still pass the current byte oracle.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def tier_of(size: int | None, insn_count: int | None) -> str:
    n = max(insn_count or 0, (size or 0) // 4)
    return ("tiny" if n < 20 else "small" if n < 60 else
            "medium" if n < 150 else "large" if n < 300 else "huge")


def exact_history(payload: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    records = payload.get("functions", payload.get("records", []))
    if not isinstance(records, list):
        raise ValueError("provenance report has no function record list")
    result: dict[str, list[dict[str, object]]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(
                record.get("function"), str):
            continue
        exact = []
        for history in record.get("history", []):
            if not isinstance(history, dict) or history.get("exact") is not True:
                continue
            paths = sorted({
                str(change["path"])
                for change in history.get("changes", [])
                if isinstance(change, dict)
                and str(change.get("path", "")).endswith(".c")
                and change.get("status") in ("A", "M", "R")
            })
            exact.append({
                "sha": history.get("sha"),
                "subject": history.get("subject"),
                "paths": paths,
            })
        if exact:
            result[str(record["function"]).upper()] = exact
    return result


def frontier(conn: sqlite3.Connection, payload: dict[str, object], *,
             exclude_tiers: set[str] | None = None) -> list[dict[str, object]]:
    history = exact_history(payload)
    rows = conn.execute(
        "select f.addr, f.name, f.size, f.insn_count, "
        "coalesce(max(a.score), 0), count(a.id) "
        "from functions f left join attempts a on a.func_addr=f.addr "
        "where not exists (select 1 from attempts x "
        "                  where x.func_addr=f.addr and x.exact=1) "
        "group by f.addr,f.name,f.size,f.insn_count"
    ).fetchall()
    result = []
    for addr, name, size, insn_count, best, attempts in rows:
        symbol = f"FUNC_{addr & 0xFFFFFFFF:08X}"
        commits = history.get(symbol)
        if not commits:
            continue
        tier = tier_of(size, insn_count)
        if exclude_tiers and tier in exclude_tiers:
            continue
        result.append({
            "address": f"0x{addr & 0xFFFFFFFF:08X}",
            "function": name,
            "historical_symbol": "func_" + symbol[5:],
            "tier": tier,
            "size": size or 0,
            "instructions": insn_count or 0,
            "best_score": float(best),
            "attempts": attempts,
            "exact_history": commits,
        })
    result.sort(key=lambda row: (
        int(row["size"]), int(row["instructions"]), float(row["best_score"])),
        reverse=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--provenance", type=Path,
        default=Path("eval/results/provenance.json"))
    parser.add_argument("--exclude-tier", action="append", default=[])
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.provenance.read_text(encoding="utf-8"))
    conn = sqlite3.connect(args.db, timeout=60)
    rows = frontier(conn, payload, exclude_tiers=set(args.exclude_tier))
    rows = rows[:max(args.limit, 0)]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        commit = row["exact_history"][0]
        paths = ",".join(commit["paths"]) or "?"
        print(f"{row['address']} {row['function']:<44} "
              f"{row['tier']:<6} {row['size']:>5}B "
              f"{row['best_score']:>7.3f}%  {commit['sha']}  {paths}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Which frame members moved between two builds, and what the KB says about them.

The frame is rebuilt from a live query, so it can move for two different reasons: the pool changed
(a function gained an exact attempt) or the query's ordering ties broke differently. Those have
different fixes, so the members are named rather than counted.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"


def names(payload: str) -> list[dict]:
    rows = json.loads((BASE / payload).read_text(encoding="utf-8"))["rows"]
    return [{"function": r["function"], "tier": r.get("tier"), "size": r.get("size"),
             "converted": r["sequence"]["compiled"]} for r in rows]


a, b = names("class-frame.json"), names("class-frame-r4.json")
na, nb = [r["function"] for r in a], [r["function"] for r in b]
only_a = [r for r in a if r["function"] not in nb]
only_b = [r for r in b if r["function"] not in na]
print(f"in stored only ({len(only_a)}):")
for r in only_a:
    print(f"   {r['tier']:7} {r['size']:6} {r['function']}")
print(f"in r4 only ({len(only_b)}):")
for r in only_b:
    print(f"   {r['tier']:7} {r['size']:6} {r['function']}")

conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    print("\nKB state for the movers:")
    for r in only_a + only_b:
        row = conn.execute(
            "select count(*), sum(coalesce(a.exact,0)), max(a.id) from attempts a "
            "join functions f on f.addr = a.func_addr where f.name = ?", (r["function"],)).fetchone()
        print(f"   {r['function']:22} attempts={row[0]:4} exact={row[1]} last_id={row[2]}")
    print("\nnewest attempts in the KB:")
    for row in conn.execute(
            "select a.id, f.name, a.compiled, a.exact, a.created_at from attempts a "
            "join functions f on f.addr = a.func_addr order by a.id desc limit 12"):
        print(f"   id={row[0]:6} {row[1]:24} compiled={row[2]} exact={row[3]} {row[4]}")
finally:
    conn.close()

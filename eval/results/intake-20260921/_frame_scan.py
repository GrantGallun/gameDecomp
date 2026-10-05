"""Read-only reconnaissance for the size-bucketed frame: what sizes exist, what failure classes exist.

NOT a measurement. It writes nothing to the KB and calls no model. It exists so the frame built next
is chosen from measured counts rather than from a guess about "the largest failure class".
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

KB = Path.home() / "decomp/kb-sbk1.sqlite"

conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    print("tables:", [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table' order by name")])
    for table in ("functions", "attempts"):
        try:
            cols = [r[1] for r in conn.execute(f"pragma table_info({table})")]
            print(f"{table} columns:", cols)
            print(f"{table} rows:", conn.execute(f"select count(*) from {table}").fetchone()[0])
        except sqlite3.Error as exc:
            print(f"{table}: {exc}")

    print("\n--- no exact attempt, by size bucket (functions that HAVE attempts) ---")
    rows = conn.execute(
        "select f.name, max(f.size) as size, count(a.id) as n, sum(coalesce(a.exact,0)) as solved "
        "from attempts a join functions f on f.addr = a.func_addr "
        "group by f.name having solved = 0").fetchall()
    print("total unsolved-with-attempts:", len(rows))
    edges = [(0, 16), (16, 32), (32, 64), (64, 128), (128, 256), (256, 512), (512, 10**9)]
    buckets = Counter()
    for _, size, _, _ in rows:
        size = size or 0
        for lo, hi in edges:
            if lo <= size < hi:
                buckets[(lo, hi)] += 1
                break
    for lo, hi in edges:
        hi_s = "+" if hi > 10**8 else str(hi)
        print(f"  [{lo:4d},{hi_s:>5}) {buckets[(lo, hi)]:4d}")

    print("\n--- size range of unsolved-with-attempts ---")
    sizes = sorted((s or 0) for _, s, _, _ in rows)
    if sizes:
        import statistics
        print("min", sizes[0], "p25", sizes[len(sizes)//4], "median", statistics.median(sizes),
              "p75", sizes[3*len(sizes)//4], "max", sizes[-1])

    print("\n--- top 12 functions by size, unsolved ---")
    for name, size, n, _ in sorted(rows, key=lambda r: -(r[1] or 0))[:12]:
        print(f"  {size:6d}  attempts={n:3d}  {name}")

    print("\n--- what the compiler said, most recent attempt per unsolved function ---")
    counts = Counter()
    for name, _, _, _ in rows:
        row = conn.execute(
            "select a.compiler_stderr from attempts a join functions f on f.addr = a.func_addr "
            "where f.name = ? order by a.id desc limit 1", (name,)).fetchone()
        text = (row[0] if row else "") or ""
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        key = "Syntax Error" if "Syntax Error" in text else (
            "undefined" if "undefined" in text else (
                "Selector" if "Selector" in text else ("<empty/compiles>" if not first else first[:60])))
        counts[key] += 1
    for key, n in counts.most_common(15):
        print(f"  {n:4d}  {key}")
finally:
    conn.close()

"""Which near misses are REAL: name them, and drop anything already solved.

Two corrections before any of this can be called a target list, both of them things this project has
already been burned by:

  * `attempt.exact` is a LOWER BOUND. The audit found 12 functions with a passing ROM-backed function-extent
    certificate whose attempts never set `exact`, and all 12 are already `matched` in `src/`. Counting those
    as "near misses that a search closed" would be a recount, not a match.
  * a near miss keyed by ADDRESS is not runnable: `build_context` takes a function name. The names come from
    the KB's own symbol table, and a miss without one is reported rather than guessed.

Read-only. Run in WSL:
  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.results.public-pairs-20260921._near_miss_named
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

KB = Path.home() / "decomp/kb-sbk1.sqlite"
HERE = Path(__file__).resolve().parent
OUT = HERE / "near-miss-named.json"
SRC = Path.home() / "decomp/sbk1/src"

conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    tables = [row[0] for row in conn.execute(
        "select name from sqlite_master where type='table' order by name")]
    columns = {table: [row[1] for row in conn.execute(f"pragma table_info({table})")]
               for table in tables}
    # Whatever the schema calls a symbol, find it rather than assuming one spelling.
    name_tables = {table: cols for table, cols in columns.items()
                   if any(c.lower() in ("name", "symbol", "func_name") for c in cols)}
    rows = conn.execute(
        "select func_addr, func_name, max(score) best, count(*) compiles "
        "from attempts where coalesce(compiled,0)=1 "
        "  and func_addr not in (select func_addr from attempts where coalesce(exact,0)=1) "
        "group by func_addr order by best desc").fetchall() \
        if "func_name" in columns.get("attempts", []) else None
    if rows is None:
        rows = [(addr, None, best, compiles) for addr, best, compiles in conn.execute(
            "select func_addr, max(score) best, count(*) compiles from attempts "
            "where coalesce(compiled,0)=1 "
            "  and func_addr not in (select func_addr from attempts where coalesce(exact,0)=1) "
            "group by func_addr order by best desc").fetchall()]
finally:
    conn.close()

# WHAT IS ALREADY IN THE BUILD. `src/**` is the reference decomp; a function whose name appears there as a
# definition is solved whatever the attempt log says.
solved_names = set()
if SRC.is_dir():
    import re
    for path in SRC.rglob("*.c"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"(?m)^[A-Za-z_][\w \t*]*?\b(\w+)\s*\([^;{]*\)\s*\{", text):
            solved_names.add(match.group(1))

entries, unnamed, already = [], [], []
for addr, name, best, compiles in rows:
    if not name:
        unnamed.append({"func_addr": addr, "best": best})
        continue
    if name in solved_names:
        already.append({"function": name, "best": best})
        continue
    entries.append({"func_addr": addr, "function": name, "best": best, "compiles": compiles})

payload = {"schema_version": 1, "kind": "near-miss-named", "kb": str(KB),
           "candidates": len(entries), "already_in_src": already, "unnamed": unnamed,
           "note": ("`already_in_src` are excluded because a function present in the reference decomp is "
                    "solved regardless of what its attempts recorded; `unnamed` are not runnable because "
                    "`build_context` takes a name"),
           "functions": entries}
OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"candidates": len(entries), "excluded_already_in_src": len(already),
                  "excluded_unnamed": len(unnamed)}, indent=2))
print("\ntop 20 runnable near misses:")
for row in entries[:20]:
    print(f"  {row['function']:38} best={row['best']:>8}  addr={row['func_addr']:#010x} "
          f"compiles={row['compiles']}")
if already:
    print(f"\nalready in src ({len(already)}): {[row['function'] for row in already[:10]]}")
print(f"\nwritten {OUT}")

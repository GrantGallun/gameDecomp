"""How many functions are one operator away? The population today's result pays on.

`regalloc-search` closed 1 of the 17 frontend-passing development states and improved 8. The question this
answers is the only one that matters for whether that is a one-off: how many functions in the real target
already COMPILE (so the oracle can score them) and are not exact -- because that is the population the
operator acts on.

Read-only. Run in WSL:
  PYTHONPATH=/mnt/c/Code/gameComp python -m eval.results.public-pairs-20260921._near_miss
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

KB = Path.home() / "decomp/kb-sbk1.sqlite"
OUT = Path(__file__).resolve().parent / "near-miss-population.json"

conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    total = conn.execute("select count(*) from attempts").fetchone()[0]
    functions = conn.execute("select count(distinct func_addr) from attempts").fetchone()[0]
    exact = conn.execute(
        "select count(distinct func_addr) from attempts where coalesce(exact,0)=1").fetchone()[0]
    # A function is a NEAR MISS when some attempt compiled and none was exact: the oracle produced an
    # object, so the candidate is scorable, and the repair catalog has a candidate to work on.
    rows = conn.execute(
        "select func_addr, count(*) compiles, max(score) best "
        "from attempts where coalesce(compiled,0)=1 "
        "  and func_addr not in (select func_addr from attempts where coalesce(exact,0)=1) "
        "group by func_addr order by best desc").fetchall()
    columns = [row[1] for row in conn.execute("pragma table_info(attempts)")]
finally:
    conn.close()

payload = {"schema_version": 1, "kind": "near-miss-population",
           "kb": str(KB), "attempts": total, "functions_attempted": functions,
           "functions_with_an_exact_attempt": exact,
           "near_miss_functions": len(rows),
           "note": ("a near miss = at least one attempt compiled and none was exact. These are the "
                    "functions `regalloc-search` can act on: the oracle can score them, and the "
                    "candidate is real C rather than a non-compiling draft."),
           "best_score_bands": {}, "functions": [{"func_addr": r[0], "compiles": r[1], "best": r[2]}
                                                 for r in rows],
           "columns_available": columns}
bands = {"90+": 0, "80-90": 0, "70-80": 0, "60-70": 0, "<60": 0}
for _addr, _compiles, best in rows:
    score = float(best or 0.0)
    key = ("90+" if score >= 90 else "80-90" if score >= 80 else "70-80" if score >= 70
           else "60-70" if score >= 60 else "<60")
    bands[key] += 1
payload["best_score_bands"] = bands
OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in payload.items() if k != "functions"}, indent=2))
print(f"\ntop 15 by best score:")
for row in payload["functions"][:15]:
    print(f"  {row['func_addr']:#010x}  best={row['best']:>8}  compiles={row['compiles']}")
print(f"\nwritten {OUT}")

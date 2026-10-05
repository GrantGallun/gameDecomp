"""Read-only: how the main KB searched a function before this run (why was a reachable exact not harvested?)."""
import json, sqlite3, sys
from pathlib import Path
kb = sqlite3.connect(f"file:{Path.home()}/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
for name in sys.argv[1:]:
    rows = kb.execute("select a.strategy, count(*), max(a.score) from attempts a join functions f on f.addr=a.func_addr "
                      "where f.name=? group by 1 order by 2 desc", (name,)).fetchall()
    fam = {}
    for s, n, m in rows:
        key = (s or "").split(":")[0]
        fam[key] = fam.get(key, 0) + n
    print(name, sum(n for _s, n, _m in rows), "attempts;", dict(sorted(fam.items(), key=lambda kv: -kv[1])[:8]))

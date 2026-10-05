"""Is the draft-score effect just function difficulty? Stratify by instruction count. Read-only.

Prediction if the BASIN theory holds: within each size band, functions whose best root draft is
>=95 go exact far more often than those below 95. If the gap collapses inside size bands, the
draft score was a proxy for size and the theory is wrong.
"""
import collections
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
children = {r[0] for r in db.execute("select distinct child_attempt_id from attempt_edges")}
size = {a: n for a, n in db.execute("select addr, insn_count from functions")}
root_best, exact = {}, set()
for i, addr, score, comp, ex in db.execute(
        "select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0) from attempts"):
    if ex:
        exact.add(addr)
    if comp and score is not None and i not in children:
        root_best[addr] = max(root_best.get(addr, -1), score)
cells = collections.defaultdict(lambda: [0, 0])
for addr, s in root_best.items():
    n = size.get(addr) or 0
    band = "<=30" if n <= 30 else "31-80" if n <= 80 else "81-200" if n <= 200 else ">200"
    cell = cells[(band, ">=95" if s >= 95 else "85-95" if s >= 85 else "<85")]
    cell[0] += 1
    cell[1] += addr in exact
print(f"{'insns':8s} {'root':6s} {'fns':>5s} {'exact':>6s}")
for band in ("<=30", "31-80", "81-200", ">200"):
    for root in (">=95", "85-95", "<85"):
        n, e = cells[(band, root)]
        if n:
            print(f"{band:8s} {root:6s} {n:5d} {e/n:6.1%}")

"""Does the starting draft decide the outcome? Best root-draft score per function vs whether the
function ever went exact. Read-only.
"""
import collections
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
children = {r[0] for r in db.execute("select distinct child_attempt_id from attempt_edges")}
root_best = {}
exact = set()
best = {}
for i, addr, score, comp, ex in db.execute(
        "select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0) from attempts"):
    if ex:
        exact.add(addr)
    if comp and score is not None:
        best[addr] = max(best.get(addr, -1), score)
        if i not in children:
            root_best[addr] = max(root_best.get(addr, -1), score)
buckets = collections.defaultdict(lambda: [0, 0, 0.0])
for addr, s in root_best.items():
    b = "<50" if s < 50 else "50-70" if s < 70 else "70-85" if s < 85 else "85-95" if s < 95 else ">=95"
    buckets[b][0] += 1
    buckets[b][1] += addr in exact
    buckets[b][2] += (best[addr] - s)
print("best compiled ROOT draft score -> functions, exact now, mean gain the search added")
for b in ("<50", "50-70", "70-85", "85-95", ">=95"):
    n, e, g = buckets[b]
    if n:
        print(f"  {b:6s} {n:5d} functions  exact {e:4d} ({e/n:5.1%})  mean search gain {g/n:5.1f}")
no_root = [a for a in best if a not in root_best]
print("functions with no compiled root draft:", len({*best} - {*root_best}),
      "| exact among them:", len(set(no_root) & exact))

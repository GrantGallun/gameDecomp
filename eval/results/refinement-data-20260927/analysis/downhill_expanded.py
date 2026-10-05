"""Is going downhill ever the way to a match, once exploration is controlled for?

paths_to_exact.py found 0.01% of score-down children have an exact descendant. The obvious
confound: the search keeps the best node, so a downhill child is rarely expanded, and a node
nobody searched from cannot have descendants. Here only EXPANDED children count, and the
yield is also given per descendant compiled (a bigger subtree gets more chances). Read-only.

    python3 downhill_expanded.py   (cwd holding campaign.sqlite, as paths_to_exact.py)
"""
import collections
import sqlite3
import sys

sys.setrecursionlimit(100000)
db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)
att = {r[0]: r[1:] for r in db.execute(
    "select id, score, coalesce(compiled,0), coalesce(exact,0) from attempts")}
kids = collections.defaultdict(list)
for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
    kids[p].append(c)

memo = {}


def subtree(n):
    """(has an exact descendant, number of descendants) -- DAG-safe via memo, cycles cut."""
    if n in memo:
        return memo[n]
    memo[n] = (False, 0)
    exact, size = False, 0
    for k in kids.get(n, ()):
        if k not in att:
            continue
        e, s = subtree(k)
        exact = exact or bool(att[k][2]) or e
        size += 1 + s
    memo[n] = (exact, size)
    return memo[n]


rows = collections.defaultdict(lambda: [0, 0, 0])   # kind -> [expanded, with exact, descendants]
for p, cs in list(kids.items()):
    if p not in att or not att[p][1]:
        continue
    for c in cs:
        if c not in att or not att[c][1] or att[c][2]:
            continue
        a, b = att[p][0] or 0, att[c][0] or 0
        kind = "up" if b > a else "flat" if b == a else "down"
        exact, size = subtree(c)
        if size == 0:
            continue                       # never searched from: says nothing either way
        drop = a - b
        band = kind if kind != "down" else ("down <1" if drop < 1 else "down 1-5" if drop < 5
                                            else "down >=5")
        for k in (kind, band) if band != kind else (kind,):
            r = rows[k]
            r[0] += 1
            r[1] += exact
            r[2] += size
for k in ("up", "flat", "down", "down <1", "down 1-5", "down >=5"):
    n, e, s = rows[k]
    print(f"{k:9s} expanded {n:7d}  with exact descendant {e:5d} ({100*e/max(n,1):5.2f}%)  "
          f"descendants {s:8d}  exacts per 1k descendants {1000*e/max(s,1):.2f}")

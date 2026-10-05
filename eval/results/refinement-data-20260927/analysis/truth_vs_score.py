"""Does object truth (solver.invariants, level by level) predict eventual matches better than the
similarity score? Read-only, campaign edges.

Prediction if the expert order is right: children that improve on object truth reach an exact
descendant more often than children that improve only the score, most visibly where the two
disagree. Bias to keep in mind: the campaign's search was SCORE-driven, so score-improving
children were expanded more -- a result favouring object truth is stronger than it looks, one
favouring the score is weaker.
"""
import collections
import sqlite3
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import invariants as inv  # noqa: E402

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
att = {r[0]: r[1:] for r in db.execute(
    "select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0) from attempts")}
kids = collections.defaultdict(list)
edges = db.execute("select parent_attempt_id, child_attempt_id from attempt_edges").fetchall()
for p, c in edges:
    kids[p].append(c)
memo = {}


def exact_desc(n):
    if n in memo:
        return memo[n]
    stack, seen, found = list(kids.get(n, ())), set(), False
    while stack and not found:
        k = stack.pop()
        if k in seen or k not in att:
            continue
        seen.add(k)
        found = bool(att[k][3])
        stack.extend(kids.get(k, ()))
    memo[n] = found
    return found


dist_cache = {}


def dist(aid):
    if aid not in dist_cache:
        row = db.execute("select diff_summary from attempts where id=?", (aid,)).fetchone()
        dist_cache[aid] = inv.distance_from_diff(row[0] or "")
    return dist_cache[aid]


cells = collections.defaultdict(lambda: [0, 0])
level_moves = collections.Counter()
for p, c in edges:
    if p not in att or c not in att:
        continue
    pa, ca = att[p], att[c]
    if pa[0] != ca[0] or not pa[2] or not ca[2] or pa[3]:
        continue
    child_exact = bool(ca[3])
    dp = dist(p)
    dc = (0,) * 6 if child_exact else dist(c)
    truth = "up" if dc < dp else "down" if dc > dp else "same"
    score = "up" if ca[1] > pa[1] else "down" if ca[1] < pa[1] else "same"
    hit = child_exact or exact_desc(c)
    cells[(score, truth)][0] += 1
    cells[(score, truth)][1] += hit
    lp, lc = inv.level(dp), inv.level(dc)
    if truth == "up" and lp != lc:
        level_moves[(lp, lc or "exact")] += 1
print("score x object-truth  ->  edges, share with an exact descendant (or exact itself)")
for s in ("up", "same", "down"):
    for t in ("up", "same", "down"):
        n, h = cells[(s, t)]
        if n:
            print(f"  score {s:4s} truth {t:4s}  {n:7d}  {h / n:7.2%}")
print("\nlevel transitions on truth-improving edges (from -> to):")
for (a, b), n in level_moves.most_common(15):
    print(f"  {n:6d}  {a} -> {b}")

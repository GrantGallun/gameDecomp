"""Are no-op steps stepping stones? Read-only, campaign edges.

A no-op child compiles to its parent's object, so it inherits the parent's prospects: its 3.23%
"led to a match" (flat_split.out) could be the parent's prospects, not the no-op's C change.
Direct test: when a step's subtree reaches an exact, does the exact source KEEP the lines that step
changed? Kept => the change was part of the key (a stepping stone). Dropped => incidental.
Baseline: the same measure for score-up steps.

Also: of no-op children with an exact descendant, how many of their PARENTS reached an exact by a
path that avoids the no-op (a sibling path) -- if nearly all, the no-op added nothing.

    python3 noop_stepping.py   (cwd holding campaign.sqlite)
"""
import collections
import hashlib
import sqlite3
import sys
from collections import deque

db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)


def body(diff):
    lines = (diff or "").splitlines()
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines = lines[1:]
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()


att = {}
for i, score, comp, ex, diff in db.execute(
        "select id, score, coalesce(compiled,0), coalesce(exact,0), diff_summary from attempts"):
    att[i] = (score or 0.0, comp, ex, body(diff))
kids = collections.defaultdict(list)
for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
    if p in att and c in att:
        kids[p].append(c)


def source(aid, cache={}):
    if aid not in cache:
        cache[aid] = db.execute("select source_code from attempts where id=?", (aid,)).fetchone()[0] or ""
    return cache[aid]


def lines(text):
    return {" ".join(l.split()) for l in text.splitlines() if l.strip()}


def nearest_exact(n, avoid=None):
    """Breadth-first nearest exact descendant of n, optionally never passing through `avoid`."""
    seen, queue = {n}, deque(kids.get(n, ()))
    while queue:
        k = queue.popleft()
        if k in seen or k == avoid:
            continue
        seen.add(k)
        if att[k][2]:
            return k
        queue.extend(kids.get(k, ()))
    return None


stats = collections.defaultdict(lambda: collections.Counter())
sibling = collections.Counter()
for p, cs in list(kids.items()):
    pa = att[p]
    if not pa[1] or pa[2]:
        continue
    for c in cs:
        ca = att[c]
        if not ca[1] or ca[2]:
            continue
        if ca[3] == pa[3]:
            kind = "no-op"
        elif ca[0] > pa[0]:
            kind = "score up"
        else:
            continue
        e = nearest_exact(c)
        if e is None:
            continue
        added = lines(source(c)) - lines(source(p))
        if not added:
            ps, cs_ = source(p), source(c)
            if ps == cs_:
                label = "identical source (re-run)"
            elif " ".join(ps.split()) == " ".join(cs_.split()):
                label = "whitespace only"
            else:
                # same set of lines, different order: a statement / declaration move. Kept if
                # the exact keeps the CHILD's order of the moved lines rather than the parent's.
                pl = [" ".join(l.split()) for l in ps.splitlines() if l.strip()]
                cl = [" ".join(l.split()) for l in cs_.splitlines() if l.strip()]
                el = [" ".join(l.split()) for l in source(e).splitlines() if l.strip()]
                moved = [l for i, l in enumerate(cl) if i >= len(pl) or pl[i] != l]
                order = [l for l in el if l in set(moved)]
                label = ("reorder: exact keeps the child's order" if order == [l for l in cl if l in set(moved)]
                         else "reorder: exact keeps the parent's order" if order == [l for l in pl if l in set(moved)]
                         else "reorder: exact has another order")
            stats[kind][label] += 1
            continue
        kept = len(added & lines(source(e))) / len(added)
        stats[kind]["all kept" if kept == 1 else "none kept" if kept == 0 else "some kept"] += 1
        if kind == "no-op":
            sibling["parent also reaches an exact avoiding the no-op"
                    if nearest_exact(p, avoid=c) is not None else "only through the no-op"] += 1
for kind, c in stats.items():
    n = sum(c.values())
    print(f"{kind}: {n} steps whose subtree reached an exact")
    for k, v in c.most_common():
        print(f"   {k:40s} {v:6d}  {v / n:6.1%}")
print("no-op steps that led to an exact:", dict(sibling))

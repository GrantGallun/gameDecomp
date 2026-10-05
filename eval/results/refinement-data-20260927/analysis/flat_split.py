"""Split 'flat' steps: NO-OP (child's instruction diff identical to the parent's: the object did
not change) vs NEUTRAL (object changed, score equal). The earlier finding that flat steps precede
matches (paths_to_exact.out) did not separate them; the flat-beam design depends on which it is.
Read-only."""
import collections
import hashlib
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
def body(diff: str) -> str:
    """The instruction diff without its two header lines, which carry the dump file's name and a
    TIMESTAMP. The first version of this script hashed the raw text, so no two diffs were ever
    equal and nearly every no-op was booked as a 'neutral' step."""
    lines = (diff or "").splitlines()
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines = lines[1:]
    return "\n".join(lines)


att = {}
for i, score, comp, ex, diff in db.execute(
        "select id, score, coalesce(compiled,0), coalesce(exact,0), diff_summary from attempts"):
    att[i] = (score, comp, ex, hashlib.sha1(body(diff).encode()).hexdigest())
kids = collections.defaultdict(list)
edges = db.execute("select parent_attempt_id, child_attempt_id from attempt_edges").fetchall()
for p, c in edges:
    kids[p].append(c)
memo = {}


def desc(n):
    if n in memo:
        return memo[n]
    memo[n] = False
    stack, seen = list(kids.get(n, ())), set()
    while stack:
        k = stack.pop()
        if k in seen or k not in att:
            continue
        seen.add(k)
        if att[k][2]:
            memo[n] = True
            return True
        stack.extend(kids.get(k, ()))
    return False


out = collections.Counter()
for p, c in edges:
    if p not in att or c not in att or not att[p][1] or not att[c][1] or att[c][2]:
        continue
    if att[c][0] != att[p][0]:
        continue
    kind = "flat-noop (identical diff)" if att[c][3] == att[p][3] else "flat-neutral (object changed)"
    out[(kind, desc(c))] += 1
for kind in ("flat-noop (identical diff)", "flat-neutral (object changed)"):
    t, d = out[(kind, False)] + out[(kind, True)], out[(kind, True)]
    print(f"{kind:32s} {t:7d}  with exact descendant {d:5d} ({d / max(t, 1):.2%})")

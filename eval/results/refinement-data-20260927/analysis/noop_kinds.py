"""Why are branch-point alternatives no-ops? Group the pilot's compiled alternatives by the KIND of
change the model said it was making, and measure how often IDO produced the parent's object anyway.
Read-only, pilot KB.
"""
import collections
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver.branch_points import diff_body  # noqa: E402

db = sqlite3.connect("file:/home/grant/decomp/experiments/redraft-pilot-20260927/pilot.sqlite?mode=ro",
                     uri=True)
KINDS = [
    ("index vs pointer arithmetic", r"index|pointer arith|subscript|\[\]"),
    ("cast / type spelling", r"\bcast|explicit type|typecast"),
    ("signedness / width", r"sign|unsigned|width|s16|u16|s8|u8|short|char\b"),
    ("temporary / local variable", r"temporar|local var|introduc|cache|reuse|alias|extra var"),
    ("statement / operand order", r"order|swap|reorder|commut|operand"),
    ("loop form", r"loop|while|for\b|do-while|goto"),
    ("condition / branch", r"condition|branch|if|else|ternary|polarity"),
    ("constant / literal form", r"constant|literal|hex|immediate|mask"),
    ("struct / field access", r"struct|field|member|->|offset"),
]


def kind(why: str) -> str:
    low = (why or "").lower()
    for name, pattern in KINDS:
        if re.search(pattern, low):
            return name
    return "other"


parent_diff = {}
stats = collections.defaultdict(lambda: collections.Counter())
for aid, compiled, diff, raw, parent in db.execute(
        "select id, coalesce(compiled,0), diff_summary, raw_response, parent_attempt_id from attempts "
        "where strategy='model-branch-point'"):
    try:
        why = json.loads(raw or "{}").get("why", "")
    except ValueError:
        why = ""
    if parent not in parent_diff:
        row = db.execute("select diff_summary from attempts where id=?", (parent,)).fetchone()
        parent_diff[parent] = diff_body(row[0] if row else "")
    k = kind(why)
    if not compiled:
        stats[k]["not-compiled"] += 1
    elif diff_body(diff) == parent_diff[parent]:
        stats[k]["no-op"] += 1
    else:
        stats[k]["changed object"] += 1
print(f"{'kind of change the model named':32s} {'n':>5s} {'no-op':>7s} {'changed':>8s} {'no-compile':>10s}")
for k, c in sorted(stats.items(), key=lambda kv: -sum(kv[1].values())):
    n = sum(c.values())
    compiled = c["no-op"] + c["changed object"]
    print(f"{k:32s} {n:5d} {c['no-op'] / max(compiled, 1):7.0%} {c['changed object'] / max(compiled, 1):8.0%}"
          f" {c['not-compiled'] / n:10.0%}")

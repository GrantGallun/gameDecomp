"""Show the model's winning edits (parent -> model child that is exact or improved), as unified diffs.

    python3 model_wins.py [campaign.sqlite] [N]
"""
import difflib
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
rows = db.execute("""
    select f.name, p.score, c.score, c.exact, p.source_code, c.source_code, e.action
    from attempt_edges e join attempts p on p.id=e.parent_attempt_id
    join attempts c on c.id=e.child_attempt_id join functions f on f.addr=c.func_addr
    where length(coalesce(c.raw_response,''))>0 and c.strategy like 'modelrepair-d%'
      and p.compiled=1 and c.compiled=1 and c.exact=1
    order by c.id""").fetchall()
print(len(rows), "exact-producing model edits; showing", min(n, len(rows)), "evenly spaced")
step = max(1, len(rows) // n)
for name, ps, cs, ex, a, b, action in rows[::step][:n]:
    diff = [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if not l.startswith(("+++", "---", "@@"))]
    print(f"\n### {name}  {ps:.1f} -> {cs:.1f}  action={str(action)[:80]!r}")
    for line in diff[:10]:
        print("   ", line[:150])

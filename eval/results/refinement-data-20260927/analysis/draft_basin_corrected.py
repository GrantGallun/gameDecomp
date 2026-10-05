"""Correction to draft_basin.py / draft_basin_size.py: those counted roots that were ALREADY EXACT
as '>=95 roots that went exact', inflating conversion. Here: exact-on-arrival roots are separated,
and conversion is measured only from NON-exact roots."""
import collections, sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro", uri=True)
children = {r[0] for r in db.execute("select distinct child_attempt_id from attempt_edges")}
exact = {r[0] for r in db.execute("select distinct func_addr from attempts where exact=1")}
size = dict(db.execute("select addr, insn_count from functions"))
root_exact, best = set(), {}
for aid, addr, score, ex in db.execute("select id, func_addr, score, coalesce(exact,0) from attempts "
                                       "where coalesce(compiled,0)=1 and score is not null"):
    if aid in children:
        continue
    if ex:
        root_exact.add(addr)
    elif addr not in best or score > best[addr]:
        best[addr] = score
print("functions whose root draft was exact on arrival:", len(root_exact))
cells = collections.defaultdict(lambda: [0, 0])
for addr, s in best.items():
    if addr in root_exact:
        continue
    n = size.get(addr) or 0
    sb = "<=30" if n <= 30 else "31-80" if n <= 80 else "81-200" if n <= 200 else ">200"
    b = ">=95" if s >= 95 else "85-95" if s >= 85 else "<85"
    cells[(sb, b)][0] += 1; cells[(sb, b)][1] += addr in exact
print("NON-exact best root -> converted to exact by search")
for sb in ("<=30", "31-80", "81-200", ">200"):
    for b in (">=95", "85-95", "<85"):
        n, e = cells[(sb, b)]
        if n: print(f"  {sb:7s} {b:6s} {n:4d}  {e/n:6.1%}")

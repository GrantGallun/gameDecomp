"""Frame A: the original site-edits frame (regression). Frame B: width frame U branch-routing frame."""
import json, sqlite3
from pathlib import Path
HERE = Path(__file__).resolve().parent
R = HERE.parent
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
def band(ic): return "small" if ic < 50 else "medium" if ic < 150 else "large+"
a = [{"name": r["name"], "ledger": "campaign", "attempt_id": r["attempt_id"], "size": r.get("size", 0),
      "band": "small"} for r in json.loads((R / "site-edits-20260929" / "frame.json").read_text())]
(HERE / "frame-A.json").write_text(json.dumps(a, indent=1))
width = {r["name"]: r for r in json.loads((R / "width-edits-20260929" / "frame.json").read_text())}
branch = [n for n, k in json.loads((R / "structural-residual-20260929" / "branch_fire_new.json").read_text()).items() if k]
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
best = {}
for ledger, db in (("campaign", C), ("kb", K)):
    amap = {x: (n, ic) for x, n, ic in db.execute("select addr,name,insn_count from functions")}
    for x, sc, i in db.execute("select func_addr, max(score), id from attempts where compiled=1 and exact=0 group by func_addr"):
        n, ic = amap.get(x, (None, 0))
        if n in branch and n not in exact and (n not in best or sc > best[n]["score"]):
            best[n] = {"name": n, "ledger": ledger, "attempt_id": i, "score": sc, "size": ic, "band": band(ic)}
b = dict(width)
for n, r in best.items():
    b.setdefault(n, r)
for n in b:
    b[n]["in_width"] = n in width; b[n]["in_branch"] = n in best
(HERE / "frame-B.json").write_text(json.dumps(list(b.values()), indent=1))
print("A", len(a), "B", len(b), "overlap", sum(1 for r in b.values() if r["in_width"] and r["in_branch"]))

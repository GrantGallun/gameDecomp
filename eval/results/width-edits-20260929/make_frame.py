"""Frame: unsolved functions whose best compiling attempt (either ledger) has a local/expression extension difference."""
import json, sqlite3
from pathlib import Path
HERE = Path(__file__).resolve().parent
sites = json.loads((HERE.parent / "structural-residual-20260929" / "width_sites.json").read_text())
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
best = {}
for ledger, db in (("campaign", C), ("kb", K)):
    amap = {a: (n, ic) for a, n, ic in db.execute("select addr,name,insn_count from functions")}
    for a, sc, i in db.execute("select func_addr, max(score), id from attempts where compiled=1 and exact=0 group by func_addr"):
        n, ic = amap.get(a, (None, 0))
        if n and n not in exact and (n not in best or sc > best[n]["score"]):
            best[n] = {"name": n, "ledger": ledger, "attempt_id": i, "score": sc, "size": ic}
frame = []
for n, kinds in sorted(sites.items()):
    if n in best and any(k.endswith("@local/expression") for k in kinds):
        row = best[n]
        row["band"] = "small" if row["size"] < 50 else "medium" if row["size"] < 150 else "large+"
        row["extension_units"] = sum(kinds.values())
        frame.append(row)
(HERE / "frame.json").write_text(json.dumps(frame, indent=1))
import collections
print(len(frame), collections.Counter(r["band"] for r in frame), collections.Counter(r["ledger"] for r in frame))

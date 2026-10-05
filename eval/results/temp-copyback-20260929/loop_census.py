"""Fire census (no compiles): which unsolved best states does counted_loop / temp_copyback / unaligned_copy match?"""
import collections, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import counted_loop
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
best = {}
for db in (C, K):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, s in db.execute("select func_addr, max(score), source_code from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n and n not in exact and (n not in best or sc > best[n][0]): best[n] = (sc, s)
fired = {}
rotated_any = 0
for n, (sc, s) in best.items():
    if "for (;;)" in (s or "") and "> 0) {" in (s or ""): rotated_any += 1
    v = list(counted_loop.variants(s or "", n))
    if v: fired[n] = {"score": sc, "labels": [l for l, _ in v]}
print("unsolved best states:", len(best), " with a for(;;) under a >0 guard:", rotated_any, " counted_loop fires:", len(fired))
print(sorted(((round(v["score"], 1), n) for n, v in fired.items()), reverse=True)[:25])
Path(__file__).with_name("loop_census.json").write_text(json.dumps(fired, indent=1))

"""For fired variants: already compiled in a ledger (by source sha)? For silent gated: what shape is missing? Crash site."""
import sys, sqlite3, collections, json, hashlib, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import branch_shape, workspace
HERE = Path(__file__).resolve().parent
rows = json.loads((HERE / "branch_fire.json").read_text())
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
best = {}
for db in (C, K):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, d, s in db.execute("select func_addr, max(score), diff_summary, source_code from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n in rows and (n not in best or sc > best[n][0]): best[n] = (sc, d, s)
seen = collections.Counter(); per_fn_new = {}
for n, r in rows.items():
    sc, d, src = best[n]
    try:
        out = list(branch_shape.variants(src or "", n, d or ""))
    except Exception:
        print("CRASH", n); traceback.print_exc(limit=3); continue
    new = 0
    for label, kind, cand in out:
        shas = {hashlib.sha256(x.encode()).hexdigest() for x in (cand, workspace._candidate_compile_source(Path("/home/grant/decomp/sbk1"), cand))}
        hit = any(db.execute("select 1 from attempts where source_sha256 in (%s) limit 1" % ",".join("?" * len(shas)), tuple(shas)).fetchone() for db in (C, K))
        seen["already compiled" if hit else "never compiled"] += 1
        new += not hit
    if out: per_fn_new[n] = new
print("fired variants:", dict(seen))
print("functions with >=1 never-compiled variant:", sum(1 for v in per_fn_new.values() if v))
json.dump(per_fn_new, open(HERE / "branch_fire_new.json", "w"), indent=1)

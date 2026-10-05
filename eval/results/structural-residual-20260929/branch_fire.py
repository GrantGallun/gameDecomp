"""Fire test: does solver.branch_shape emit any variant on the control-flow functions? (no compiles)"""
import sys, sqlite3, collections, json, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import branch_shape
HERE = Path(__file__).resolve().parent
rows = json.loads((HERE / "control_flow.json").read_text())
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
best = {}
for db in (C, K):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, d, s in db.execute("select func_addr, max(score), diff_summary, source_code from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n in rows and (n not in best or sc > best[n][0]): best[n] = (sc, d, s)
fire = collections.Counter(); fam = collections.Counter(); errors = collections.Counter()
by_gate = collections.Counter()
for n, r in rows.items():
    sc, d, src = best[n]
    try:
        out = list(branch_shape.variants(src or "", n, d or ""))
    except Exception as exc:
        errors[type(exc).__name__ + ":" + str(exc)[:60]] += 1; out = []
    labels = {v[1] for v in out}
    r["variants"] = len(out); r["families"] = sorted(labels)
    fire["fired" if out else "silent"] += 1
    by_gate[("gate" if r["any_gate_matches"] else "no-gate", "fired" if out else "silent")] += 1
    for l in labels: fam[l] += 1
print(dict(fire)); print("by gate:", dict(by_gate)); print("families:", fam.most_common()); print("errors:", errors.most_common(5))
(HERE / "branch_fire.json").write_text(json.dumps(rows, indent=1))

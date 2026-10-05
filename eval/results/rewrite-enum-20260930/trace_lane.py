import json, sqlite3, sys
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import rule_miner, site_edits
rows = json.load(open("/mnt/c/Code/gameDecomp/eval/results/rewrite-enum-20260930/diagnose_g3.json"))
db = sqlite3.connect("/home/grant/decomp/runs/rewrite-enum-20260930/g3b.sqlite")
n_prop = n_edit = 0
for r in [x for x in rows if x["g_applicable"]][:12]:
    fn = r["function"]
    src, diff = db.execute("select a.source_code, a.diff_summary from attempts a join functions f on f.addr=a.func_addr "
                           "where f.name=? and a.strategy like 'g:baseline%' order by a.id limit 1", (fn,)).fetchone()
    props = rule_miner.proposals(src, fn, diff, limit=8)
    g = [p[1] for p in props if p[1].startswith("G:")]
    edits = site_edits._mined_edits(src, fn, diff)
    ge = [e.kind for e in edits if e.kind.startswith("mined:G:")]
    n_prop += bool(g); n_edit += bool(ge)
    print(fn, len(props), len(g), len(edits), len(ge))
print(n_prop, n_edit)

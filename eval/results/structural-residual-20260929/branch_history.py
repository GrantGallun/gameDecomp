"""For functions where branch_shape fires on the best state: was any branch-shape family ever compiled, and did
register search run from the best attempt (a child whose parent is the best attempt)?"""
import sqlite3, json, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
new = json.loads((HERE / "branch_fire_new.json").read_text())
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
fams = ("select_else", "split_merge", "at_inline", "empty_then_return", "dup_return_merge", "m2c_struct_copy", "o1_register")
out = collections.Counter(); rows = {}
for n, k in new.items():
    if not k: continue
    a = C.execute("select addr from functions where name=?", (n,)).fetchone()
    if not a: out["not in campaign"] += 1; continue
    strat = [s for (s,) in C.execute("select strategy from attempts where func_addr=?", a)]
    any_branch = any(any(f in (s or "") for f in fams) for s in strat)
    best = C.execute("select id, strategy, score from attempts where func_addr=? and compiled=1 order by score desc, id limit 1", a).fetchone()
    kids = C.execute("select count(*) from attempts where parent_attempt_id=?", (best[0],)).fetchone()[0]
    rs = sum(1 for s in strat if "regalloc" in (s or ""))
    out["ever tried a branch-shape family" if any_branch else "never tried any branch-shape family"] += 1
    out["best attempt has children" if kids else "best attempt has NO children"] += 1
    out["register search ran at all" if rs else "register search never ran"] += 1
    rows[n] = {"best_strategy": best[1], "best_score": best[2], "children_of_best": kids, "regalloc_attempts": rs,
               "branch_family_attempts": any_branch, "attempts": len(strat)}
print(dict(out))
strat_best = collections.Counter(r["best_strategy"].split(":")[0] for r in rows.values())
print("best-attempt strategy:", strat_best.most_common(8))
(HERE / "branch_history.json").write_text(json.dumps(rows, indent=1))

"""Old vs new structural count on every unsolved function's best compiling attempt (read-only)."""
import sys, sqlite3, collections, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import signals
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
size = {n: ic for n, ic in C.execute("select name,insn_count from functions")}
best = {}
for db in (C, K):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, d in db.execute("select func_addr, max(score), diff_summary from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n and n not in exact and (n not in best or sc > best[n][0]): best[n] = (sc, d)
real = signals.line_map
res = collections.Counter(); moved_to_reg = 0; rows = []
for n, (sc, d) in best.items():
    signals.line_map = lambda diff: None
    old = signals.analyse(d or "", sc, False, True)
    signals.line_map = real
    new = signals.analyse(d or "", sc, False, True)
    res["functions"] += 1
    res["old structural>0"] += old.structural > 0
    res["new structural>0"] += new.structural > 0
    res["old structural units"] += old.structural
    res["new structural units"] += new.structural
    res["branch_shift units"] += new.branch_shift
    res["units moved to regalloc"] += new.regalloc - old.regalloc
    if old.structural and not new.structural: res["structural cleared entirely"] += 1
    axes = [a for a in ("structural", "layout", "reloc", "regalloc", "ordering", "immediate") if getattr(new, a)]
    old_axes = [a for a in ("structural", "layout", "reloc", "regalloc", "ordering", "immediate") if getattr(old, a)]
    res["old single-axis"] += len(old_axes) == 1
    res["new single-axis"] += len(axes) == 1
    rows.append({"function": n, "insns": size.get(n), "score": sc, "old_structural": old.structural,
                 "new_structural": new.structural, "branch_shift": new.branch_shift, "new_axes": axes})
print(json.dumps(dict(res), indent=1))
Path(__file__).with_name("metric_fix.json").write_text(json.dumps({"summary": dict(res), "functions": rows}, indent=1))

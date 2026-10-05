"""Current residual census: unsolved functions (neither ledger exact), best compiled attempt, signal axes."""
import sys, sqlite3, collections, json
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import signals
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
funcs = {n: (a, ic) for a, n, ic in C.execute("select addr,name,insn_count from functions")}
best = {}
for db in (C, K):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, d in db.execute("select func_addr, max(score), diff_summary from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n and (n not in best or sc > best[n][0]): best[n] = (sc, d)
def band(ic): return "small" if ic < 50 else "medium" if ic < 150 else "large" if ic < 400 else "huge"
tab = collections.defaultdict(lambda: collections.Counter())
dom = collections.defaultdict(collections.Counter)
sole = collections.defaultdict(collections.Counter)
scores = collections.defaultdict(list)
AX = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")
for n, (a, ic) in funcs.items():
    if n in exact: continue
    b = band(ic or 0)
    tab[b]["unsolved"] += 1
    if n not in best: tab[b]["never_compiled"] += 1; continue
    sc, d = best[n]
    scores[b].append(sc)
    s = signals.analyse(d or "", sc, False, True)
    v = {x: getattr(s, x) for x in AX}
    present = [x for x in AX if v[x]]
    for x in present: dom[b][x] += 1
    if len(present) == 1: sole[b][present[0]] += 1
    if not present: tab[b]["no_axis(diff empty/padding)"] += 1
    if s.instr_delta != 0: tab[b]["instr_count_differs"] += 1
    if sc >= 95: tab[b]["score>=95"] += 1
out = {b: {"counts": dict(tab[b]), "axis_present": dict(dom[b]), "sole_axis": dict(sole[b]),
           "median_best": sorted(scores[b])[len(scores[b])//2] if scores[b] else None} for b in tab}
out["total_exact_union"] = len(exact & set(funcs))
print(json.dumps(out, indent=1))

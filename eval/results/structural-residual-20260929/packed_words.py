"""Class census: words assembled from bytes (lbu + sll 24/16/8 + or) on one side vs a word load on the other."""
import collections, sqlite3, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import diffrepair
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
best = {}
for db in (C, K):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, d in db.execute("select func_addr, max(score), diff_summary from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n and n not in exact and (n not in best or sc > best[n][0]): best[n] = (sc, d)
def packed(stream):
    n = 0
    for i, x in enumerate(stream):
        p = x.replace(",", " ").split()
        if len(p) >= 4 and p[0] == "sll" and p[-1] in ("0x18", "24"):
            src = p[2]
            if any(y.split()[0] == "lbu" and y.replace(",", " ").split()[1] == src for y in stream[max(0, i - 5):i] if y.split()):
                n += 1
    return n
def op_count(stream, ops): return sum(1 for x in stream if x.split() and x.split()[0] in ops)
c = collections.Counter(); ex = []
for n, (sc, d) in best.items():
    t, cand = diffrepair._streams(d or "")
    if not t: continue
    pt, pc = packed(t), packed(cand)
    ut, uc = op_count(t, ("lwl", "lwr")), op_count(cand, ("lwl", "lwr"))
    if pc > pt:
        c["candidate packs more words than target"] += 1
        c["  ...and target uses lwl/lwr where candidate doesn't" if ut > uc else "  ...target has no extra lwl/lwr (plain lw?)"] += 1
        ex.append((n, round(sc, 1), pt, pc, ut, uc))
    elif pt > pc:
        c["target packs more words than candidate"] += 1
    if ut != uc: c["lwl/lwr count differs"] += 1
print(dict(c)); print(sorted(ex, key=lambda e: -e[1])[:15])

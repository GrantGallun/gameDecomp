"""Per function: children that reduced extension units, and whether their instruction distance also fell."""
import sys, sqlite3, collections, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import signals
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import extension_units
T = sqlite3.connect("file:/home/grant/decomp/runs/width-edits-20260929/trial.sqlite?mode=ro", uri=True)
names = {a: n for a, n in T.execute("select addr,name from functions")}
by = collections.defaultdict(list)
for a, strat, comp, d in T.execute("select func_addr, strategy, compiled, diff_summary from attempts order by id"):
    if comp: by[names[a]].append((strat, d))
out = collections.Counter()
for n, atts in by.items():
    base = [d for s, d in atts if s == "width-edits:baseline"]
    if not base: continue
    b_ext = extension_units(base[0]); b_ins, b_reg = signals.distances(base[0])
    best_kind = None
    for s, d in atts:
        if s == "width-edits:baseline": continue
        e = extension_units(d)
        if e >= b_ext: continue
        ins, reg = signals.distances(d)
        k = "ext down, instr distance down" if ins < b_ins else "ext down, instr distance same" if ins == b_ins else "ext down, instr distance UP"
        rank = ["ext down, instr distance down", "ext down, instr distance same", "ext down, instr distance UP"].index(k)
        if best_kind is None or rank < best_kind: best_kind = rank
    if best_kind is not None:
        out[["ext down, instr distance down", "ext down, instr distance same", "ext down, instr distance UP"][best_kind]] += 1
    else:
        out["no child reduced ext"] += 1
print(dict(out))

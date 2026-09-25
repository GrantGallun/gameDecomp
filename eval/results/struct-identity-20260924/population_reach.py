"""Reach of the chosen identity variant over the unsolved population: params/returns in multi-function groups and the
offsets those groups add beyond the function's own accesses. No compiles, no labels."""
import collections, json, statistics, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score
rows = json.loads((score.E / "facts.json").read_text())
d = json.loads((HERE / "score.json").read_text())
key = d["chosen_C"]
uf = score.solve(rows, key[0], int(key[1:]))
tup = score.tup
own, group, funcs = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
for r in rows:
    for node, off, *_ in r["accesses"]:
        n = tup(node)
        own[(r["function"], n)].add(off)
        root = uf.find(n)
        group[root].add(off)
        funcs[root].add(r["function"])
POP = Path.home() / "decomp/experiments/restart-round3-20260923/rows"
unsolved = [json.loads(p.read_text())["function"] for p in POP.glob("*.json") if not json.loads(p.read_text()).get("exact")]
present = {r["function"] for r in rows}
reached, gains = [], []
for f in unsolved:
    if f not in present:
        continue
    g_f = 0
    for k in range(4):
        n = ("P", f, k)
        root = uf.find(n)
        if len(funcs[root]) > 1:
            extra = group[root] - own[(f, n)]
            g_f += len(extra)
    if g_f:
        reached.append(f)
        gains.append(g_f)
out = {"variant": key, "unsolved_present": sum(f in present for f in unsolved), "reached": len(reached),
       "median_extra_offsets": statistics.median(gains) if gains else 0, "reached_functions": reached}
(HERE / "population_reach.json").write_text(json.dumps(out, indent=1))
print({k: v for k, v in out.items() if k != "reached_functions"})

"""Ceiling estimates (no compiles): how much of the unsolved residual the mined rules can even address."""
import collections, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import rule_miner
table = rule_miner.Table.load()
usable = {k: v for k, v in table.rules.items() if not v.get("pruned")}
covered = set().union(*[set(v["profile"]) for v in usable.values()])
covered_a = set().union(*[set(v["profile"]) for k, v in usable.items() if k.startswith("A:")])
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact, best = set(), {}
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
for db in (C, K):
    amap = {a: (n, ic) for a, n, ic in db.execute("select addr,name,insn_count from functions")}
    for a, sc, d in db.execute("select func_addr, max(score), diff_summary from attempts where compiled=1 and exact=0 group by func_addr"):
        n, ic = amap.get(a, (None, 0))
        if n and n not in exact and (n not in best or sc > best[n][0]): best[n] = (sc, d, ic)
mass = collections.Counter(); hit = collections.Counter(); hit_a = collections.Counter(); fns_full = 0; fns_any = 0
uncovered = collections.Counter()
for n, (sc, d, ic) in best.items():
    f = {k: v for k, v in rule_miner.features(d).items() if not k.startswith("class:")}
    tot = sum(f.values())
    if not tot: continue
    c = sum(v for k, v in f.items() if k in covered)
    mass["all"] += tot; hit["all"] += c; hit_a["all"] += sum(v for k, v in f.items() if k in covered_a)
    fns_any += c > 0; fns_full += c == tot
    for k, v in f.items():
        if k not in covered: uncovered[k] += v
print(f"unsolved functions: {len(best)}; usable rules: {len(usable)} (A {sum(k.startswith('A:') for k in usable)}, B {sum(k.startswith('B:') for k in usable)})")
print(f"residual instruction-feature mass addressed by some rule profile: {hit['all']}/{mass['all']} = {hit['all']/mass['all']:.1%} (Engine A alone {hit_a['all']/mass['all']:.1%})")
print(f"functions with any addressed feature: {fns_any}; with every feature addressed: {fns_full}")
print("largest unaddressed features:", uncovered.most_common(12))
# corpus bias: size distribution of Engine A's corpus vs unsolved
rows = [json.loads(l) for l in open(Path(__file__).with_name("engine-a.jsonl"))]
corp = json.load(open(Path(__file__).with_name("engine-a-corpus.json")))
band = lambda ic: "small" if (ic or 0) < 50 else "medium" if ic < 150 else "large+"
print("Engine A corpus by size:", collections.Counter(band(r["size"]) for r in corp))
print("unsolved by size:       ", collections.Counter(band(v[2]) for v in best.values()))

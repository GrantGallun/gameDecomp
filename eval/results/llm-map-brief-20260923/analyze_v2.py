"""v2 verdict (PROTOCOL-v2.md): per function and arm, best compiled score over 6 calls vs the start."""
import collections
import hashlib
import json
import math
from pathlib import Path

H = Path(__file__).resolve().parent
log = json.loads((H / "v2-log.json").read_text())
probes = {hashlib.sha256(p["source"].encode()).hexdigest(): p for p in json.loads((H / "probes-v2.json").read_text())}
results = [json.loads(l) for l in (H.parent / "population-transfer-20260922/probes.jsonl").read_text().splitlines()
           if '"v2:' in l]
start = {r["function"]: r["start"] for r in log}
best = collections.defaultdict(lambda: {"A": None, "B": None})
exact = collections.defaultdict(lambda: {"A": False, "B": False})
stats = {a: collections.Counter() for a in "AB"}
for r in log:
    stats[r["arm"]]["calls"] += 1
    stats[r["arm"]]["parsed"] += r["parsed"]
for r in results:
    p = probes.get(r["source_sha256"])
    if not p:
        continue
    arm = p["label"].split(":")[1]
    stats[arm]["compiled"] += r["compiled"]
    stats[arm]["improving_candidates"] += bool(r["compiled"] and (r["exact"] or r["score"] > p["parent_score"]))
    if r["compiled"]:
        cur = best[r["function"]][arm]
        best[r["function"]][arm] = r["score"] if cur is None else max(cur, r["score"])
    exact[r["function"]][arm] |= r["exact"]
b_better = a_better = 0
per = {}
for f in start:
    ga = (best[f]["A"] if best[f]["A"] is not None else start[f]) - start[f]
    gb = (best[f]["B"] if best[f]["B"] is not None else start[f]) - start[f]
    per[f] = {"A_gain": round(max(0, ga), 3), "B_gain": round(max(0, gb), 3), "exact_A": exact[f]["A"], "exact_B": exact[f]["B"]}
    b_better += max(0, gb) > max(0, ga)
    a_better += max(0, ga) > max(0, gb)
n = a_better + b_better
p = sum(math.comb(n, i) for i in range(b_better, n + 1)) / 2 ** n if n else None
ea, eb = sum(v["exact_A"] for v in per.values()), sum(v["exact_B"] for v in per.values())
helps = eb > ea or (eb == ea and p is not None and p <= 0.10 and b_better > a_better)
out = {"functions": len(per), "exact": {"A": ea, "B": eb}, "B_better": b_better, "A_better": a_better,
       "sign_p_one_sided": p, "stats": {a: dict(stats[a]) for a in stats}, "per_function": per,
       "verdict": "brief helps" if helps else "null"}
(H / "analysis-v2.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k != "per_function"}, indent=1))

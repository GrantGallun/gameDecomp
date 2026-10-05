import json
import math
from pathlib import Path

H = Path(__file__).resolve().parent
rows = [json.loads(p.read_text()) for p in sorted((H / "arms").glob("*.summary.json"))]
by = {}
for r in rows:
    by.setdefault(r["function"], {})[r["arm"]] = r
pairs = {f: v for f, v in by.items() if "A" in v and "B" in v}
tot = {a: {"exact": 0, "improved": 0, "invalid": 0, "compiling": 0, "calls": 0} for a in "AB"}
b_better = a_better = 0
for f, v in pairs.items():
    for a in "AB":
        r = v[a]
        tot[a]["exact"] += r["exact"]
        tot[a]["improved"] += (r["best"] or 0) > r["start"]
        tot[a]["invalid"] += r["invalid"]
        tot[a]["compiling"] += r["compiling_children"]
        tot[a]["calls"] += r["calls"]
    ga, gb = (v["A"]["best"] or 0) - v["A"]["start"], (v["B"]["best"] or 0) - v["B"]["start"]
    b_better += gb > ga
    a_better += ga > gb
n = a_better + b_better
p = sum(math.comb(n, i) for i in range(b_better, n + 1)) / 2 ** n if n else None
helps = tot["B"]["exact"] > tot["A"]["exact"] or (tot["B"]["exact"] == tot["A"]["exact"] and p is not None and p <= 0.10
                                                  and b_better > a_better)
out = {"functions": len(pairs), "totals": tot, "B_better": b_better, "A_better": a_better, "sign_p_one_sided": p,
       "verdict": "brief helps" if helps else "null"}
(H / "analysis.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))

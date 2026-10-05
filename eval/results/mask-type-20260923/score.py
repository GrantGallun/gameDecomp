import hashlib
import json
from pathlib import Path

H = Path(__file__).resolve().parent
probes = {hashlib.sha256(p["source"].encode()).hexdigest(): p for p in json.loads((H / "../mask-type-20260923/probes-narrow.json").read_text())}
res = [json.loads(l) for l in (H.parent / "population-transfer-20260922/probes.jsonl").read_text().splitlines()
       if '"narrow:' in l]
c = [r for r in res if r["source_sha256"] in probes]
comp = [r for r in c if r["compiled"]]
imp = [r for r in comp if r["exact"] or r["score"] > probes[r["source_sha256"]]["parent_score"]]
print("candidates", len(c), "compiled", len(comp), "improved", len(imp), "functions", len({r["function"] for r in imp}),
      "exact", [r["function"] for r in c if r["exact"]])
print(sorted(((round(r["score"] - probes[r["source_sha256"]]["parent_score"], 2), r["function"]) for r in imp),
             reverse=True)[:8])

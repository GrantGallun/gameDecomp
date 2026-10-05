import hashlib
import json
from pathlib import Path

H = Path(__file__).resolve().parent
probes = {hashlib.sha256(p["source"].encode()).hexdigest(): p for p in json.loads((H / "probes-compose.json").read_text())}
res = [json.loads(l) for l in (H.parent / "population-transfer-20260922/probes.jsonl").read_text().splitlines()
       if '"compose:' in l]
rows = [(r, probes[r["source_sha256"]]) for r in res if r["source_sha256"] in probes]
compiled = [(r, p) for r, p in rows if r["compiled"]]
beats_single = [(r, p) for r, p in compiled if r["exact"] or r["score"] > p["best_single"]]
# the function's best in the whole recorded search
best = {}
for path in (Path.home() / "decomp/experiments/locality-population-20260923/rows").glob("*.json"):
    row = json.loads(path.read_text())
    if row.get("best_score") is not None:
        best[row["function"]] = row["best_score"]
beats_search = [(r, p) for r, p in compiled if r["exact"] or r["score"] > best.get(r["function"], 101)]
print("merged", len(rows), "compiled", len(compiled), "beat their best single edit", len(beats_single),
      "beat the whole search's best", len(beats_search), "in", len({r["function"] for r, _ in beats_search}), "functions")
print("exact:", sorted({r["function"] for r, _ in rows if r["exact"]}))
print(sorted(((round(r["score"] - best[r["function"]], 2), r["function"]) for r, _ in beats_search), reverse=True)[:10])

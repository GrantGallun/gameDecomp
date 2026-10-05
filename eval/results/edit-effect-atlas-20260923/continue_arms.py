"""Paired continuation: 32 more compiles from the search's best (control) vs from the best merged candidate
(compose), for every function with a merged candidate. Writes the two start files for run_derived_search-style runs."""
import hashlib
import json
from pathlib import Path

H = Path(__file__).resolve().parent
PT = H.parent / "population-transfer-20260922"
pop = {p["function"]: p for p in json.loads((PT / "population.json").read_text())}
probes = {hashlib.sha256(p["source"].encode()).hexdigest(): p for p in json.loads((H / "probes-compose.json").read_text())}
res = [json.loads(l) for l in (PT / "probes.jsonl").read_text().splitlines() if '"compose:' in l]
best_merge = {}
for r in res:
    p = probes.get(r["source_sha256"])
    if p and r["compiled"] and (p["function"] not in best_merge or r["score"] > best_merge[p["function"]][0]):
        best_merge[p["function"]] = (r["score"], p["source"])
control, compose = [], []
for name, (score, src) in sorted(best_merge.items()):
    row = json.loads((Path.home() / f"decomp/experiments/locality-population-20260923/rows/{name}--locality.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    best = next(n for n in world["nodes"] if n["id"] == row["best_id"])
    base = {k: pop[name][k] for k in ("function", "addr", "insn_count", "source_attempt_id")}
    control.append({**base, "source": best["source"], "source_sha256": hashlib.sha256(best["source"].encode()).hexdigest(),
                    "start_score": row["best_score"]})
    compose.append({**base, "source": src, "source_sha256": hashlib.sha256(src.encode()).hexdigest(), "start_score": score})
(H / "starts-control.json").write_text(json.dumps(control, indent=1))
(H / "starts-compose.json").write_text(json.dumps(compose, indent=1))
print(len(control), "functions")

"""Per signature class: do forward-derived candidates compile, and do they beat the function's best-so-far?"""
import collections
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
probes = json.loads((HERE / "probes-forward.json").read_text())
parent = {hashlib.sha256(p["source"].encode()).hexdigest(): p for p in probes}
results = [json.loads(l) for l in (HERE.parent / "population-transfer-20260922/probes.jsonl").read_text().splitlines()
           if l.strip() and '"forward:' in l]
by = collections.defaultdict(lambda: collections.Counter())
fns_improved = collections.defaultdict(set)
gains = []
for r in results:
    p = parent.get(r["source_sha256"])
    if not p:
        continue
    sig = r["label"].split(":", 1)[1].rsplit(":", 1)[0]
    b = by[sig]
    b["candidates"] += 1
    b["compiled"] += r["compiled"]
    b["exact"] += r["exact"]
    if r["compiled"] and r["score"] > p["parent_score"]:
        b["improved"] += 1
        fns_improved[sig].add(r["function"])
        gains.append((round(r["score"] - p["parent_score"], 3), r["function"], sig, p["parent_score"], r["score"]))
table = {s: {**dict(c), "functions_improved": len(fns_improved[s]),
             "improve_rate": round(c["improved"] / c["candidates"], 3)} for s, c in sorted(by.items(), key=lambda kv: -kv[1]["improved"])}
(HERE / "forward-score.json").write_text(json.dumps({"by_signature": table, "top_gains": sorted(gains, reverse=True)[:15]}, indent=1))
print(f"{'signature':18} {'cands':>5} {'compiled':>8} {'improved':>8} {'fns':>4} {'rate':>6}")
for s, t in table.items():
    print(f"{s:18} {t['candidates']:5} {t['compiled']:8} {t.get('improved', 0):8} {t['functions_improved']:4} {t['improve_rate']:6}")
print("functions improved (any class):", len(set().union(*fns_improved.values())) if fns_improved else 0)
for g in sorted(gains, reverse=True)[:10]:
    print("  ", g)

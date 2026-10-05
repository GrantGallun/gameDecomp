"""Apply structural_mutations to every compiled pending function where a family fires.

    nice /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/probe_structural.py [--families a,b]

Greedy: up to 3 rounds, each keeping the best variant that improves (exact, score).
Writes structural-probe/summary.json and the best source per function.
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import structural_mutations as sm  # noqa: E402

rows = [r for r in json.loads((HERE / "pending.json").read_text()) if r["compiled"] and r["frontend"] is not False]
out = HERE / "structural-probe"
out.mkdir(exist_ok=True)
results = []
for row in rows:
    source = Path(row["source"]).read_text()
    if not any(True for _ in sm.variants(source, row["function"])):
        continue
    bench = regalloc_probe.Bench({"name": row["function"], "source": row["source"]})
    entry = {"function": row["function"], "campaign_faults": row["total_faults"]}
    try:
        base = bench.run(source, "baseline")
        best = (base, source)
        entry.update(baseline_score=base["score"], baseline_gradient=base.get("gradient"))
        for round_index in range(3):
            chosen = None
            for label, kind, variant in sm.variants(best[1], row["function"]):
                result = bench.run(variant, label)
                entry.setdefault("tried", []).append({"label": label, "round": round_index, "compiled": result["compiled"],
                                                      "score": result["score"], "exact": result["exact"],
                                                      "gradient": result.get("gradient")})
                if result["compiled"] and (result["exact"], result["score"]) > (
                        (chosen or best)[0]["exact"], (chosen or best)[0]["score"]):
                    chosen = (result, variant, label)
            if chosen is None:
                break
            best = chosen[:2]
            entry.setdefault("path", []).append(chosen[2])
            if chosen[0]["exact"]:
                break
        entry.update(best_score=best[0]["score"], best_gradient=best[0].get("gradient"), exact=best[0]["exact"],
                     outcome="exact" if best[0]["exact"] else "improved" if best[0]["score"] > base["score"] else "no_change")
        if best[1] != source:
            (out / f"{row['function']}.c").write_text(best[1])
    except Exception as error:
        entry.update(outcome="error", error=f"{type(error).__name__}: {error}"[:300])
    finally:
        bench.close()
    results.append(entry)
    print(json.dumps({k: entry.get(k) for k in ("function", "outcome", "path", "baseline_score", "best_score")}), flush=True)
(out / "summary.json").write_text(json.dumps(results, indent=1))
print(json.dumps({"functions": len(results), **dict(Counter(r["outcome"] for r in results)),
                  "by_family": dict(Counter(p.split(":")[0] for r in results if r.get("outcome") in ("exact", "improved")
                                            for p in r.get("path", [])))}))

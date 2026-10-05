"""Apply address_symbols to every pending function that passes pointer-cast address literals.

    nice /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/probe_address_symbols.py

Iterates up to 3 rounds per function (a fixed site can expose the next site's
alignment). Writes address-probe/summary.json and the best source per function.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import address_symbols  # noqa: E402

rows = [r for r in json.loads((HERE / "pending.json").read_text()) if r["compiled"]
        and re.search(r"\(void \*\)0x[0-9A-Fa-f]{5,6}\b", Path(r["source"]).read_text())]
only = {a for a in sys.argv[1:] if not a.startswith("--")}
out = HERE / ("address-probe-segments" if "--segments" in sys.argv else "address-probe")
import yaml  # noqa: E402
SEGMENTS = address_symbols.segments_from(yaml.safe_load((Path.home() / "decomp/sbk1/snowboardkids.yaml").read_text())) if "--segments" in sys.argv else None
out.mkdir(exist_ok=True)
results = []
for row in rows:
    if only and row["function"] not in only:
        continue
    bench = regalloc_probe.Bench({"name": row["function"], "source": row["source"]})
    entry = {"function": row["function"], "campaign_faults": row["total_faults"]}
    try:
        source = Path(row["source"]).read_text()
        base = bench.run(source, "baseline")
        entry.update(baseline_score=base["score"], baseline_gradient=base.get("gradient"))
        best, current = (base, source), (base, source)
        for round_index in range(3):
            improved = False
            for label, _kind, variant in address_symbols.variants(current[1], row["function"], current[0].get("_diff", ""), segments=SEGMENTS):
                result = bench.run(variant, f"{label}@{round_index}")
                entry.setdefault("tried", []).append({"label": result["label"], "compiled": result["compiled"],
                                                      "score": result["score"], "exact": result["exact"],
                                                      "gradient": result.get("gradient"),
                                                      "error": (result.get("compile_error") or "")[-200:]})
                if result["compiled"] and (result["exact"], result["score"]) > (best[0]["exact"], best[0]["score"]):
                    best, improved = (result, variant), True
                    break
            if not improved or best[0]["exact"]:
                break
            current = best
        entry.update(best_score=best[0]["score"], best_gradient=best[0].get("gradient"), exact=best[0]["exact"],
                     frontend=best[0].get("frontend_passed"),
                     outcome="exact" if best[0]["exact"] else "improved" if best[0]["score"] > base["score"] else "no_change")
        if best[1] != source:
            (out / f"{row['function']}.c").write_text(best[1])
    except Exception as error:
        entry.update(outcome="error", error=f"{type(error).__name__}: {error}"[:300])
    finally:
        bench.close()
    results.append(entry)
    print(json.dumps({k: entry.get(k) for k in ("function", "outcome", "baseline_score", "best_score",
                                                "baseline_gradient", "best_gradient", "frontend")}), flush=True)
(out / "summary.json").write_text(json.dumps(results, indent=1))
print(json.dumps(dict(Counter(r["outcome"] for r in results))))

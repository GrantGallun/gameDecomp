"""Apply relocation-name generators to relocation-mismatch targets; verify with the object oracle.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/frontend-reloc-20260914/probe_relocation.py [--only-relocation] [NAMES...]
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from eval import regalloc_probe  # noqa: E402
from solver import relocation_names  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
only_relocation = "--only-relocation" in sys.argv
rows = json.loads((HERE / "relocation-targets.json").read_text())["functions"]
if only_relocation:
    rows = [r for r in rows if not any(v for k, v in (r["faults"] or {}).items() if k != "relocation")]
if args:
    rows = [r for r in rows if r["name"] in args]
out = HERE / ("relocation-probe-only" if only_relocation else "relocation-probe")
out.mkdir(exist_ok=True)
results = []
for row in rows:
    bench = regalloc_probe.Bench(row)
    entry = {"function": row["name"]}
    try:
        source = Path(row["source"]).read_text()
        base = bench.run(source, "baseline")
        entry.update(baseline_score=base["score"], baseline_gradient=base.get("gradient"))
        best = None
        for label, _kind, variant in relocation_names.variants(source, row["name"], base.get("_diff", "")):
            result = bench.run(variant, label)
            candidate = {"label": label, "exact": result["exact"], "score": result["score"],
                         "compiled": result["compiled"], "frontend": result["frontend_passed"]}
            entry.setdefault("variants", []).append(candidate)
            if result["compiled"] and (best is None or (result["exact"], result["score"] or 0) > (best[0]["exact"], best[0]["score"] or 0)):
                best = (candidate, variant)
        if best is None:
            entry["outcome"] = "no_variant" if not entry.get("variants") else "variants_did_not_compile"
        else:
            (out / f"{row['name']}.c").write_text(best[1])
            entry["best"] = best[0]
            entry["outcome"] = ("exact" if best[0]["exact"] and best[0]["frontend"] else
                                "improved" if (best[0]["score"] or 0) > (base["score"] or 0) else
                                "same" if (best[0]["score"] or 0) == (base["score"] or 0) else "worse")
    except Exception as error:
        entry.update(outcome="error", error=f"{type(error).__name__}: {error}"[:300])
    finally:
        bench.close()
    results.append(entry)
    print(json.dumps({k: entry.get(k) for k in ("function", "outcome", "baseline_score", "best")}), flush=True)
(out / "summary.json").write_text(json.dumps(results, indent=1))
print(json.dumps(dict(Counter(r["outcome"] for r in results))))

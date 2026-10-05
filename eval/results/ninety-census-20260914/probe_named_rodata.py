"""Apply address_symbols.named_rodata to the 90+ functions with rodata_where_named sites (bench only, zero-model).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_named_rodata.py

For each variant: compiled, exact, score and the schema-3 function-boundary status. Writes named-rodata-probe.json.
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import address_symbols, workspace  # noqa: E402

classes = json.loads((HERE / "classes.json").read_text())
rows = {r["function"]: r for r in json.loads((HERE / "ninety.json").read_text())}
names = [r["function"] for r in classes["rows"] if "rodata_where_named" in r["families"]]
results = []
for function in names:
    entry = json.loads((HERE / "diffs" / f"{function}.json").read_text())
    found = list(address_symbols.named_rodata(entry["source_text"], function, entry["diff"]))
    row = {"function": function, "baseline_score": entry["score"], "residual_faults": rows[function]["faults"],
           "mapped": address_symbols.rodata_names(entry["diff"]), "variants": []}
    if found:
        bench = regalloc_probe.Bench({"name": function, "source": rows[function]["source"]})
        try:
            for label, _kind, text in found:
                attempt = workspace.score(bench.ws, bench.isolated, f"{function}_named_rodata", text, conn=bench.conn,
                                          func=function, strategy="named-rodata-probe", model="zero-model")
                boundary = ((attempt.verification or {}).get("function_boundary") or {})
                row["variants"].append({"label": label, "compiled": attempt.compiled, "exact": attempt.exact,
                                        "score": attempt.score, "boundary": boundary.get("status") or boundary.get("error"),
                                        "stderr": (attempt.compiler_stderr or "")[-200:] if not attempt.compiled else ""})
        finally:
            bench.close()
    best = max(row["variants"], key=lambda v: (v["boundary"] == "function_exact_pending_integration", v["exact"], v["score"] or 0),
               default=None)
    row["outcome"] = ("declined" if not found else "certified" if best["boundary"] == "function_exact_pending_integration"
                      else "exact" if best["exact"] else "improved" if (best["score"] or 0) > entry["score"] else "no_change")
    results.append(row)
    print(json.dumps({k: row[k] for k in ("function", "outcome", "baseline_score")}), [(v["label"], v["score"], v["boundary"]) for v in row["variants"]], flush=True)
(HERE / "named-rodata-probe.json").write_text(json.dumps(results, indent=1))
print(dict(Counter(r["outcome"] for r in results)))

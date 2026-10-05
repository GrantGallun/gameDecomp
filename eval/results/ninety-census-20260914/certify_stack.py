"""Re-score every stack-probe best source through workspace.score and report the real object verdict.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/certify_stack.py [--all]

Bench `exact` is normalized-assembly equality; the campaign's exact is the object certificate
(or schema-3 function_exact_pending_integration). Writes stack-certified.json.
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import workspace  # noqa: E402

rows = {r["function"]: r for r in json.loads((HERE / "ninety.json").read_text())}
results = []
for path in sorted((HERE / "stack-probe").glob("*.json")):
    entry = json.loads(path.read_text())
    if entry.get("outcome") != "exact" and "--all" not in sys.argv:
        continue
    source_path = path.with_suffix(".c")
    if not source_path.exists():
        continue
    function = entry["function"]
    bench = regalloc_probe.Bench({"name": function, "source": rows[function]["source"]})
    try:
        attempt = workspace.score(bench.ws, bench.isolated, f"{function}_stack_cert", source_path.read_text(),
                                  conn=bench.conn, func=function, strategy="stack-certify", model="zero-model")
        verification = attempt.verification or {}
        boundary = verification.get("function_boundary") or {}
        row = {"function": function, "probe": entry["outcome"], "score": attempt.score, "exact": attempt.exact,
               "repair_complete": workspace.repair_complete(attempt), "frontend": (attempt.frontend or {}).get("passed"),
               "status": verification.get("status"), "boundary": boundary.get("status") or boundary.get("error")}
    finally:
        bench.close()
    results.append(row)
    print(json.dumps(row), flush=True)
(HERE / "stack-certified.json").write_text(json.dumps(results, indent=1))
print(dict(Counter((r["exact"], r["repair_complete"], r["boundary"]) for r in results)))

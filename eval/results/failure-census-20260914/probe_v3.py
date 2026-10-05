"""Which census functions the main-tree certificate (schema 3 + operand-only gate) makes function-exact.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/probe_v3.py

Sources: each near/close function's campaign source, plus the address_symbols best
source where one exists. Writes v3-probe.json.
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import workspace  # noqa: E402

pending = {r["function"]: r for r in json.loads((HERE / "pending.json").read_text())}
classes = json.loads((HERE / "classes.json").read_text())
names = {r["function"] for r in classes["rows"]}
if "--segments" in sys.argv:
    sources = [(p.stem, p, "address_segments") for p in sorted((HERE / "address-probe-segments").glob("*.c"))]
else:
    sources = [(n, Path(pending[n]["source"]), "campaign") for n in sorted(names)]
    sources += [(p.stem, p, "address_symbols") for p in sorted((HERE / "address-probe").glob("*.c"))]
rows = []
for function, path, origin in sources:
    bench = regalloc_probe.Bench({"name": function, "source": str(path)})
    try:
        attempt = workspace.score(bench.ws, bench.isolated, "v3", path.read_text(), conn=bench.conn, func=function)
        boundary = (attempt.verification or {}).get("function_boundary") or {}
        row = {"function": function, "origin": origin, "score": attempt.score, "exact": attempt.exact,
               "function_exact": boundary.get("function_exact"), "schema": boundary.get("schema_version"),
               "error": boundary.get("schema_3_error") or boundary.get("error"),
               "certified": attempt.verification is not None,
               "frontend": (attempt.frontend or {}).get("passed")}
    except Exception as error:
        row = {"function": function, "origin": origin, "error": f"{type(error).__name__}: {error}"[:200]}
    finally:
        bench.close()
    rows.append(row)
    if row.get("function_exact") or row.get("exact"):
        print(json.dumps(row), flush=True)
(HERE / ("v3-probe-segments.json" if "--segments" in sys.argv else "v3-probe.json")).write_text(json.dumps(rows, indent=1))
print(json.dumps({"sources": len(rows),
                  "object_exact": sum(bool(r.get("exact")) for r in rows),
                  "function_exact": sum(bool(r.get("function_exact")) for r in rows),
                  "by_schema": dict(Counter(r.get("schema") for r in rows if r.get("function_exact"))),
                  "certified_not_exact_errors": dict(Counter((r.get("error") or "")[:70] for r in rows
                                                             if r.get("certified") and not r.get("function_exact")
                                                             and not r.get("exact")).most_common(12))}, indent=1))

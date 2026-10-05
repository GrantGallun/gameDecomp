"""Placeholder resolution, then void_pointer_units, on the still-blocked placeholder nodes (bench only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_void_units.py [NAMES...]

Reports compiled/score and the next IDO error (with its source line) per candidate. Writes void-units-probe.json.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import placeholder_declarations as pd, void_pointer_units as vpu, workspace  # noqa: E402

rows = {r["function"]: r for r in json.loads((HERE / "not_compiled.json").read_text())}
names = sys.argv[1:] or [r["function"] for r in json.loads((HERE / "placeholder-probe.json").read_text())
                         if r["outcome"] == "still_blocked"]
results = []
for function in names:
    source = Path(rows[function]["source"]).read_text()
    entry = {"function": function, "candidates": []}
    bench = regalloc_probe.Bench({"name": function, "source": rows[function]["source"]})
    try:
        assembly = (bench.ws / "target.s").read_text()
        seeds, _ = pd.propose(source, function, pd.header_names(bench.isolated, source))
        for seed_label, seed in seeds or [("original", source)]:
            lowered, report = vpu.propose(seed, function, assembly)
            entry["report"] = {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}
            if lowered == seed:
                continue
            attempt = workspace.score(bench.ws, bench.isolated, f"{function}_void_units", lowered, conn=bench.conn,
                                      func=function, strategy="void-units-probe", model="zero-model")
            stderr = attempt.compiler_stderr or ""
            line = re.search(r"line (\d+): ([^\n]*)", stderr)
            text = lowered.splitlines()[int(line.group(1)) - 1].strip()[:150] if line and int(line.group(1)) <= len(lowered.splitlines()) else None
            entry["candidates"].append({"seed": seed_label, "compiled": attempt.compiled, "score": attempt.score,
                                        "frontend": (attempt.frontend or {}).get("passed"),
                                        "error": line.group(2)[:90] if line else (stderr[-200:] if not attempt.compiled else None),
                                        "line": text})
    except Exception as error:
        entry["error"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        bench.close()
    best = max(entry["candidates"], key=lambda c: (c["compiled"], c["score"] or 0), default={})
    entry["outcome"] = ("error" if "error" in entry else "declined" if not entry["candidates"]
                        else "compiles" if best.get("compiled") else "still_blocked")
    entry["best"] = best
    results.append(entry)
    print(f"{entry['outcome']:13} {function[:40]:40} score={best.get('score')} report={entry.get('report')} next={best.get('error')} | {best.get('line')}",
          flush=True)
(HERE / "void-units-probe.json").write_text(json.dumps(results, indent=1))
print(dict(Counter(r["outcome"] for r in results)))

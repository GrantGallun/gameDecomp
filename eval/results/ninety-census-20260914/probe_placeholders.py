"""Apply solver.placeholder_declarations to the non-compiling nodes whose source carries placeholder shapes (bench only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_placeholders.py

Reports per variant: compiled, score, and the new first IDO error line (what blocks next).
Writes placeholder-probe.json.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import placeholder_declarations as pd, workspace  # noqa: E402

rows = json.loads((HERE / "not_compiled.json").read_text())
results = []
for row in rows:
    source = Path(row["source"]).read_text()
    if not pd.signals(source):
        continue
    entry = {"function": row["function"], "old_error": row["message"], "old_line": row.get("line_text"), "variants": []}
    bench = None
    try:
        bench = regalloc_probe.Bench({"name": row["function"], "source": row["source"]})
        candidates, report = pd.propose(source, row["function"], pd.header_names(bench.isolated, source))
        entry["report"] = report
        for label, text in candidates:
            attempt = workspace.score(bench.ws, bench.isolated, f"{row['function']}_placeholders", text, conn=bench.conn,
                                      func=row["function"], strategy="placeholder-probe", model="zero-model")
            stderr = attempt.compiler_stderr or ""
            line = re.search(r"line (\d+): (.*)", stderr)
            text_line = text.splitlines()[int(line.group(1)) - 1].strip()[:120] if line and int(line.group(1)) <= len(text.splitlines()) else None
            entry["variants"].append({"label": label, "compiled": attempt.compiled, "score": attempt.score,
                                      "frontend": (attempt.frontend or {}).get("passed"),
                                      "first_error": line.group(2)[:90] if line else stderr[-160:] if not attempt.compiled else None,
                                      "first_error_line": text_line})
    except Exception as error:
        entry["error"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        if bench is not None:
            bench.close()
    compiled = [v for v in entry["variants"] if v["compiled"]]
    entry["outcome"] = ("error" if "error" in entry else "declined" if not entry["variants"] else
                        "compiles" if compiled else "still_blocked")
    results.append(entry)
    best = max(entry["variants"], key=lambda v: (v["compiled"], v["score"] or 0), default={})
    print(f"{entry['outcome']:13} {row['function'][:40]:40} score={best.get('score')} next={best.get('first_error')} | {best.get('first_error_line')}", flush=True)
(HERE / "placeholder-probe.json").write_text(json.dumps(results, indent=1))
print(dict(Counter(r["outcome"] for r in results)))

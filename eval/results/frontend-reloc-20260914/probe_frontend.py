"""Apply frontend fix-its to every frontend-rejected target and verify with the object oracle.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/frontend-reloc-20260914/probe_frontend.py

Pass per function: the fixed source passes the project frontend AND compiles with a
score no lower than the baseline. `exact` is reported from the byte certificate.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from eval import regalloc_probe  # noqa: E402
from solver import frontend_fixits  # noqa: E402

rows = json.loads((HERE / "frontend-targets.json").read_text())["functions"]
only = set(sys.argv[1:])
out = HERE / "frontend-probe"
out.mkdir(exist_ok=True)
results = []
for row in rows:
    if only and row["name"] not in only:
        continue
    bench = regalloc_probe.Bench(row)
    try:
        source = Path(row["source"]).read_text()
        base = bench.run(source, "baseline")
        command = base.get("_frontend_command")
        if not command:
            results.append({"function": row["name"], "outcome": "no_frontend_command"})
            continue
        fixed, log = frontend_fixits.propose(bench.isolated, source, command, bench.ws)
        entry = {"function": row["name"], "baseline_score": base["score"], "baseline_exact": base["exact"],
                 "baseline_frontend": base["frontend_passed"], "rounds": log}
        if fixed is None:
            entry["outcome"] = "fixits_did_not_pass_frontend"
        else:
            result = bench.run(fixed, "frontend-fixits")
            entry.update(score=result["score"], exact=result["exact"], frontend_passed=result["frontend_passed"],
                         compiled=result["compiled"])
            (out / f"{row['name']}.c").write_text(fixed)
            entry["outcome"] = ("exact" if result["exact"] and result["frontend_passed"] else
                                "frontend_fixed_score_kept" if result["frontend_passed"] and result["score"] >= base["score"] else
                                "frontend_fixed_score_dropped" if result["frontend_passed"] else "frontend_still_rejected")
    except Exception as error:  # one function never stops the batch
        entry = {"function": row["name"], "outcome": "error", "error": f"{type(error).__name__}: {error}"[:300]}
    finally:
        bench.close()
    results.append(entry)
    print(json.dumps({k: entry.get(k) for k in ("function", "outcome", "baseline_score", "score", "exact")}), flush=True)
(out / "summary.json").write_text(json.dumps(results, indent=1))
from collections import Counter
print(json.dumps(dict(Counter(r["outcome"] for r in results))))

"""lowered_candidates (placeholders + void_pointer_units) on ALL non-compiling pending nodes (bench only, zero-model).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_draft_lowering.py [--workers 2]

Reads not_compiled.json. Per function: whether any candidate compiles, best score, next IDO error.
Writes draft-lowering/<fn>.json and draft-lowering-summary.json.
"""
import argparse
import json
import multiprocessing
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "draft-lowering"


def one(row):
    target = OUT / f"{row['function']}.json"
    if target.exists():
        return json.loads(target.read_text())
    sys.path.insert(0, str(HERE.parents[2]))
    from eval import regalloc_probe
    from solver import placeholder_declarations as pd, void_pointer_units as vpu, workspace
    function = row["function"]
    source = Path(row["source"]).read_text()
    entry = {"function": function, "old_error": row["message"], "candidates": []}
    bench = None
    try:
        bench = regalloc_probe.Bench({"name": function, "source": row["source"]})
        assembly = (bench.ws / "target.s").read_text()
        entry["signals"] = {"placeholders": pd.signals(source), "void_units": vpu.signals(source, function)}
        for label, code in vpu.lowered_candidates(source, function, pd.header_names(bench.isolated, source), assembly):
            attempt = workspace.score(bench.ws, bench.isolated, f"{function}_lowering", code, conn=bench.conn,
                                      func=function, strategy="draft-lowering-probe", model="zero-model")
            stderr = attempt.compiler_stderr or ""
            line = re.search(r"line (\d+): ([^\n]*)", stderr)
            entry["candidates"].append({"label": label, "compiled": attempt.compiled, "score": attempt.score,
                                        "frontend": (attempt.frontend or {}).get("passed"),
                                        "error": line.group(2)[:90] if line else (stderr[-160:] if not attempt.compiled else None)})
    except Exception as error:
        entry["error"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        if bench is not None:
            bench.close()
    best = max(entry["candidates"], key=lambda c: (c["compiled"], c["frontend"] is True, c["score"] or 0), default={})
    entry["best"] = best
    entry["outcome"] = ("error" if "error" in entry else "declined" if not entry["candidates"]
                        else "compiles" if best.get("compiled") else "still_blocked")
    target.write_text(json.dumps(entry, indent=1))
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    rows = json.loads((HERE / "not_compiled.json").read_text())
    results = []
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for entry in pool.imap_unordered(one, rows):
            results.append(entry)
            best = entry.get("best") or {}
            print(f"{entry['outcome']:13} {entry['function'][:40]:40} score={best.get('score')} fe={best.get('frontend')} "
                  f"label={best.get('label')} next={best.get('error')} {entry.get('error', '')}", flush=True)
    summary = {"functions": len(results), "outcomes": dict(Counter(r["outcome"] for r in results)),
               "compiles_frontend_ok": sum(1 for r in results if r["outcome"] == "compiles" and r["best"].get("frontend") is True),
               "best_labels": dict(Counter((r.get("best") or {}).get("label") for r in results if r["outcome"] == "compiles")),
               "next_errors": dict(Counter(re.sub(r"'[^']*'", "'X'", (r.get("best") or {}).get("error") or "")[:60]
                                           for r in results if r["outcome"] == "still_blocked").most_common(12))}
    (HERE / "draft-lowering-summary.json").write_text(json.dumps({**summary, "rows": results}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

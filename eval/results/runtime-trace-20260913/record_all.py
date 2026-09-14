"""Record every unmatched function the attract-mode demo reaches, in multi-function sessions.

Windows Python, repo root as cwd:

    python eval/results/runtime-trace-20260913/record_all.py --out-name record-all-1 [--limit 5 --duration 120]

Inputs are frozen files, not live queries: `map-20260913.json` (dashboard /api/map
snapshot) selects campaign categories `partial` and `compile_blocked`, and
`coverage-1/coverage.json` supplies which were reached and how often. `every`
spreads each function's recorded calls across roughly the first half of the
demo loop. Functions already recorded in an earlier session (`--skip-from`) are
not re-armed.
"""
import argparse
import json
from pathlib import Path

from eval import project64_trace

HERE = Path(__file__).resolve().parent
PORTABLE = "C:/Code/gameDecomp/eval/results/runtime-capture-20260912/portable-project64-v4"
ROM = "C:/Code/gameDecomp/eval/results/runtime-capture-20260912/pilot-rom.z64"
CATEGORIES = {"partial", "compile_blocked"}
MAX_CALLS = 6


def plan(limit=None, skip=()):
    functions = json.loads((HERE / "map-20260913.json").read_text())["functions"]
    coverage = {int(k, 16): v for k, v in json.loads((HERE / "coverage-1/coverage.json").read_text())["targets"].items()}
    rows = []
    for fn in sorted(functions, key=lambda f: f["address"]):
        if fn["category"] not in CATEGORIES or fn["address"] not in coverage or fn["name"] in skip:
            continue
        if not fn.get("size") or fn["size"] % 4:
            continue
        rows.append({"name": fn["name"], "entry": fn["address"], "end": fn["address"] + fn["size"],
                     "every": max(1, coverage[fn["address"]] // (2 * MAX_CALLS)),
                     "category": fn["category"], "coverage_calls": coverage[fn["address"]]})
    return rows[:limit] if limit else rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-name", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--skip-from", type=Path, action="append", default=[],
                        help="earlier session directory whose recorded functions are skipped")
    args = parser.parse_args()
    skip = set()
    for folder in args.skip_from:
        skip |= {p.parent.name for p in folder.glob("*/call-*.json")}
    functions = plan(args.limit, skip)
    job = {"schema_version": 1, "kind": "multi", "functions": [
               {k: f[k] for k in ("name", "entry", "end", "every")} for f in functions],
           "max_calls": MAX_CALLS, "max_events": 150_000, "max_window_ms": 8000, "max_abandons": 3,
           "max_depth": 4, "duration_seconds": args.duration,
           "portable_dir": PORTABLE, "rom_path": ROM,
           "output_dir": (HERE / args.out_name).as_posix()}
    print(json.dumps({"functions": len(functions), "skipped": len(skip)}), flush=True)
    receipt = project64_trace.run_job(job)
    (HERE / args.out_name / "plan.json").write_text(json.dumps(functions, indent=1))
    states = receipt.get("functions", {})
    summary = {"status": receipt.get("status"), "elapsed": round(receipt.get("elapsed_seconds", 0)),
               "functions_with_calls": len(receipt.get("calls") or {}),
               "complete": sum(s.get("recorded", 0) >= MAX_CALLS for s in states.values()),
               "disabled": sum(bool(s.get("disabled")) for s in states.values()),
               "never_seen": sum(s.get("seen", 0) == 0 for s in states.values()),
               "cleanup": receipt.get("cleanup"), "errors": receipt.get("error") or receipt.get("organize_error")}
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

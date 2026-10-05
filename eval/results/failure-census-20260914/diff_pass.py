"""Compile each near/close pending function's current campaign source; keep its full aligned difference.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/diff_pass.py [--max-faults 12] [--workers 2]

Isolated benches, private KB copy, no model, nothing written to the campaign.
One JSON per function in diffs/ (skipped when present, so the pass resumes).
"""
import argparse
import json
import multiprocessing
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))


def one(row):
    from eval import regalloc_probe
    out = HERE / "diffs" / f"{row['function']}.json"
    if out.exists():
        return row["function"], "cached"
    entry = {k: row[k] for k in ("function", "score", "instructions", "faults", "total_faults", "dominant", "semantic",
                                 "jobs", "stalled")}
    bench = None
    try:
        bench = regalloc_probe.Bench({"name": row["function"], "source": row["source"]})
        source = Path(row["source"]).read_text()
        attempt_name = f"{row['function']}_census"
        from solver import workspace
        attempt = workspace.score(bench.ws, bench.isolated, attempt_name, source, conn=bench.conn,
                                  func=row["function"], strategy="failure-census", model="zero-model")
        verification = attempt.verification or {}
        entry.update(compiled=bool(attempt.compiled), exact=bool(attempt.exact), fresh_score=attempt.score,
                     frontend=(attempt.frontend or {}).get("passed"),
                     certificate_status=verification.get("status"),
                     boundary_error=(verification.get("function_boundary") or {}).get("error"),
                     diff=(attempt.diff or "")[:20000])
        target = bench.ws / "target_object_dump_normalized.s"
        dump = bench.ws / f"{attempt_name}_object_dump_normalized.s"
        if attempt.compiled and dump.is_file():
            from solver import regalloc_signature
            report = regalloc_signature.compare(target.read_text(), dump.read_text())
            entry["compare"] = report.to_dict(limit=400)
        entry["target_dump"] = target.read_text()[:40000] if target.is_file() else None
        entry["candidate_dump"] = dump.read_text()[:40000] if dump.is_file() else None
        entry["source_text"] = source
    except Exception as error:  # one function never stops the pass
        entry["error"] = f"{type(error).__name__}: {error}"[:400]
    finally:
        if bench is not None:
            bench.close()
    out.write_text(json.dumps(entry, indent=1))
    return row["function"], entry.get("error") or ("exact" if entry.get("exact") else "ok")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-faults", type=int, default=12)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    (HERE / "diffs").mkdir(exist_ok=True)
    rows = [r for r in json.loads((HERE / "pending.json").read_text())
            if r["compiled"] and r["frontend"] is not False and r["total_faults"] <= args.max_faults]
    rows.sort(key=lambda r: (r["total_faults"], r["instructions"] or 0))
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for index, (name, status) in enumerate(pool.imap_unordered(one, rows), 1):
            print(f"{index}/{len(rows)} {name} {status}", flush=True)


if __name__ == "__main__":
    main()

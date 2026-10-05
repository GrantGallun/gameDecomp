"""Compile each 90+ pending function's current campaign source and keep its full aligned difference.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/diff_pass.py [--workers 3] [--min-score 90]

Same bench as failure-census-20260914/diff_pass.py (isolated, private KB copy, no model, nothing
written to the campaign). One JSON per function in diffs/, skipped when present so the pass resumes.
"""
import argparse
import json
import multiprocessing
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval/results/failure-census-20260914"))


def one(row):
    import diff_pass as base
    base.HERE = HERE                      # write into this census's diffs/
    return base.one(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=90)
    args = parser.parse_args()
    (HERE / "diffs").mkdir(exist_ok=True)
    rows = [r for r in json.loads((HERE / "ninety.json").read_text()) if r["score"] >= args.min_score]
    for r in rows:
        r.setdefault("dominant", max(r["faults"], key=r["faults"].get) if r["faults"] else None)
    rows.sort(key=lambda r: (-r["score"], r["instructions"] or 0))
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for index, (name, status) in enumerate(pool.imap_unordered(one, rows), 1):
            print(f"{index}/{len(rows)} {name} {status}", flush=True)


if __name__ == "__main__":
    main()

"""Preregistered A/B (PREREGISTRATION.md): diverse beam arms D (all generators) and E (old generators).

    python3 eval/results/diverse-beam-20260915/ab.py --out eval/results/diverse-beam-20260915/run-1 [--workers 2]
    python3 eval/results/diverse-beam-20260915/ab.py --out .../replication --arms B --limit 10

One JSON line per (function, arm) in OUT/rows.jsonl; resumable (finished pairs are skipped). Arms A and B
are the uopt-guided-20260915 settings; D and E add `diverse=True`.
"""
import argparse
import json
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval import regalloc_probe  # noqa: E402
from solver import regalloc_mutations, regalloc_search, workspace  # noqa: E402

COHORT = ROOT / "eval/results/regalloc-20260913/cohort.json"
BUDGET, BEAM, DEPTH = 150, 3, 4
OLD_GENERATORS = {"A", "E"}
DIVERSE = {"D", "E"}


def done_pairs(rows_path):
    done = set()
    if rows_path.is_file():
        for line in rows_path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:                        # a partially written line while the parent appends
                continue
            done.add((r["function"], r["arm"]))
    return done


def run_function(args):
    row, out, arms = args
    name = row["name"]
    done = done_pairs(Path(out) / "rows.jsonl")
    results = []
    for arm in arms:
        if (name, arm) in done:
            continue
        bench = regalloc_probe.Bench(row)
        source = Path(row["source"]).read_text()
        saved = regalloc_mutations.typed_field_rereads, regalloc_mutations.narrow_truth_tests
        saved_const = regalloc_mutations.constant_store_locals
        # Added to the main tree after this run started (2026-09-15); every arm here predates it.
        regalloc_mutations.constant_store_locals = lambda *a, **k: iter(())
        if arm in OLD_GENERATORS:
            regalloc_mutations.typed_field_rereads = lambda *a, **k: iter(())
            regalloc_mutations.narrow_truth_tests = lambda *a, **k: iter(())
        started = time.monotonic()
        try:
            def compile_candidate(candidate, label):
                tag = f"{name}_ab{arm}_{time.time_ns()}"
                attempt = workspace.score(bench.ws, bench.isolated, tag, candidate, conn=bench.conn, func=name,
                                          strategy="diverse-beam-ab", model="zero-model", run_id=bench.run_id)
                dump = bench.ws / f"{tag}_object_dump_normalized.s"
                text = dump.read_text() if attempt.compiled and dump.is_file() else None
                if bench.target_text is None and (bench.ws / "target_object_dump_normalized.s").is_file():
                    bench.target_text = (bench.ws / "target_object_dump_normalized.s").read_text()
                for path in bench.ws.glob(tag + "*"):
                    if path.is_file():
                        path.unlink(missing_ok=True)
                return regalloc_search.Compiled(bool(attempt.compiled), bool(attempt.exact), text, attempt.diff or "")

            baseline = compile_candidate(source, "baseline")
            outcome = regalloc_search.search(name, source, compile_candidate, bench.target_text or "", budget=BUDGET,
                                             beam=BEAM, depth=DEPTH, baseline=baseline, diverse=arm in DIVERSE)
            result = {"function": name, "arm": arm, **outcome.summary(), "seconds": round(time.monotonic() - started, 1),
                      "baseline_exact": baseline.exact}
        except Exception as error:                   # one function never stops the run
            result = {"function": name, "arm": arm, "error": f"{type(error).__name__}: {error}"[:400]}
        finally:
            regalloc_mutations.typed_field_rereads, regalloc_mutations.narrow_truth_tests = saved
            regalloc_mutations.constant_store_locals = saved_const
            bench.close()
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--arms", default="DE")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = sorted((r for r in json.loads(COHORT.read_text())["functions"] if r.get("cohort") == "regalloc_plus_le2"),
                  key=lambda r: r["name"])
    if args.only:
        rows = [r for r in rows if r["name"] in args.only]
    if args.limit:
        rows = rows[:args.limit]
    with (args.out / "rows.jsonl").open("a") as log, multiprocessing.Pool(args.workers) as pool:
        for results in pool.imap_unordered(run_function, [(r, str(args.out), args.arms) for r in rows]):
            for result in results:
                log.write(json.dumps(result) + "\n")
                log.flush()
                print(json.dumps({k: result.get(k) for k in ("function", "arm", "exact", "compiles", "error")}),
                      flush=True)


if __name__ == "__main__":
    main()

"""Preregistered A/B (PREREGISTRATION.md): register search arms A (old generators), B (all), C (all + trace).

    python3 eval/results/uopt-guided-20260915/ab.py --out eval/results/uopt-guided-20260915/run-1 [--workers 3]

One JSON line per (function, arm) in OUT/rows.jsonl; resumable (finished pairs are skipped).
"""
import argparse
import json
import multiprocessing
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval import regalloc_probe  # noqa: E402
from solver import regalloc_mutations, regalloc_search, uopt_diagnosis, workspace  # noqa: E402

COHORT = ROOT / "eval/results/regalloc-20260913/cohort.json"
TRACE_CC = Path.home() / "decomp/tools-src/ido-trace/cc"
MOTIVATING = {"updateCharacterSelectRosterIcons", "updateRaceUiScorePopupSlideIn", "updateRaceSetupNamePlateSlideIn",
              "updateRaceUiCrashScorePopupSlideIn", "updateRaceUiTrickScorePopupSlideIn",
              "updateTimeTrialRecordDeltaPopupSlideIn"}
BUDGET, BEAM, DEPTH = 150, 3, 4


def run_function(args):
    row, out = args
    name = row["name"]
    done = set()
    rows_path = Path(out) / "rows.jsonl"
    if rows_path.is_file():
        # The parent appends while workers read: skip a partially written (or I/O-truncated) line instead of
        # crashing the pool (run-1 died at 414/435 rows on exactly that during a WSL stall).
        for line in rows_path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            done.add((r["function"], r["arm"]))
    results = []
    for arm in ("A", "B", "C"):
        if (name, arm) in done:
            continue
        bench = regalloc_probe.Bench(row)
        source = Path(row["source"]).read_text()
        saved = regalloc_mutations.typed_field_rereads, regalloc_mutations.narrow_truth_tests
        if arm == "A":
            regalloc_mutations.typed_field_rereads = lambda *a, **k: iter(())
            regalloc_mutations.narrow_truth_tests = lambda *a, **k: iter(())
        started = time.monotonic()
        try:
            dumps = {}

            def compile_candidate(candidate, label):
                tag = f"{name}_ab{arm}_{time.time_ns()}"
                attempt = workspace.score(bench.ws, bench.isolated, tag, candidate, conn=bench.conn, func=name,
                                          strategy="uopt-guided-ab", model="zero-model", run_id=bench.run_id)
                dump = bench.ws / f"{tag}_object_dump_normalized.s"
                text = dump.read_text() if attempt.compiled and dump.is_file() else None
                if bench.target_text is None and (bench.ws / "target_object_dump_normalized.s").is_file():
                    bench.target_text = (bench.ws / "target_object_dump_normalized.s").read_text()
                for path in bench.ws.glob(tag + "*"):
                    if path.is_file():
                        path.unlink(missing_ok=True)
                return regalloc_search.Compiled(bool(attempt.compiled), bool(attempt.exact), text, attempt.diff or "")

            baseline = compile_candidate(source, "baseline")
            target = bench.target_text or ""

            def trace(candidate, label, compiled):
                if not compiled.compiled or not compiled.dump:
                    return None
                texts = uopt_diagnosis.traced_compile(bench.ws, bench.isolated, candidate, TRACE_CC, name)
                if texts is None:
                    return None
                return uopt_diagnosis.diagnose(target, compiled.dump, texts["level5"], texts["level6"],
                                               texts["ugen"], name)

            outcome = regalloc_search.search(name, source, compile_candidate, target, budget=BUDGET, beam=BEAM,
                                             depth=DEPTH, baseline=baseline, trace=trace if arm == "C" else None)
            result = {"function": name, "arm": arm, **outcome.summary(), "seconds": round(time.monotonic() - started, 1),
                      "motivating": name in MOTIVATING, "baseline_exact": baseline.exact}
        except Exception as error:                               # one function never stops the run
            result = {"function": name, "arm": arm, "error": f"{type(error).__name__}: {error}"[:400]}
        finally:
            regalloc_mutations.typed_field_rereads, regalloc_mutations.narrow_truth_tests = saved
            bench.close()
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = [r for r in json.loads(COHORT.read_text())["functions"] if r.get("cohort") == "regalloc_plus_le2"]
    if args.only:
        rows = [r for r in rows if r["name"] in args.only]
    with (args.out / "rows.jsonl").open("a") as log, multiprocessing.Pool(args.workers) as pool:
        for results in pool.imap_unordered(run_function, [(r, str(args.out)) for r in rows]):
            for result in results:
                log.write(json.dumps(result) + "\n")
                log.flush()
                print(json.dumps({k: result.get(k) for k in ("function", "arm", "exact", "compiles", "trace_calls",
                                                             "first_decisions", "error")}), flush=True)


if __name__ == "__main__":
    main()

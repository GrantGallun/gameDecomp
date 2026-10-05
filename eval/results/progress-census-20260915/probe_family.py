"""Register search (main-tree generators) from each function's CURRENT campaign source, budget as the campaign.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/progress-census-20260915/probe_family.py --out DIR NAME...
    ... --labels opcode:nop->li     (every function carrying that classify.py label)
    ... --root const_store          (start from the first constant_store_locals proposal)

One JSON line per function in DIR/rows.jsonl (resumable); exact sources in DIR/exact/.
"""
import argparse
import json
import multiprocessing
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))


def run(args):
    name, out, budget, diverse, root, enable, live = args
    from eval import regalloc_probe
    from solver import regalloc_mutations, regalloc_search, workspace
    if live:                                     # the frozen campaign generator set
        regalloc_mutations.typed_field_rereads = lambda *a, **k: iter(())
        regalloc_mutations.narrow_truth_tests = lambda *a, **k: iter(())
    row = {r["function"]: r for r in json.loads((HERE / "pending.json").read_text())}[name]
    source = Path(row["source"]).read_text()
    bench = regalloc_probe.Bench({"name": name, "source": row["source"]})
    started = time.monotonic()
    try:
        def compile_candidate(candidate, label):
            tag = f"{name}_fam{time.time_ns()}"
            attempt = workspace.score(bench.ws, bench.isolated, tag, candidate, conn=bench.conn, func=name,
                                      strategy="family-probe", model="zero-model", run_id=bench.run_id)
            dump = bench.ws / f"{tag}_object_dump_normalized.s"
            text = dump.read_text() if attempt.compiled and dump.is_file() else None
            if bench.target_text is None and (bench.ws / "target_object_dump_normalized.s").is_file():
                bench.target_text = (bench.ws / "target_object_dump_normalized.s").read_text()
            for path in bench.ws.glob(tag + "*"):
                if path.is_file():
                    path.unlink(missing_ok=True)
            return regalloc_search.Compiled(bool(attempt.compiled), bool(attempt.exact), text, attempt.diff or "")

        if root == "const_store":
            proposal = next(regalloc_mutations.constant_store_locals(source, name), None)
            if proposal is None:
                return {"function": name, "error": "no const_store_local proposal"}
            source = proposal[2]
        baseline = compile_candidate(source, "baseline")
        outcome = regalloc_search.search(name, source, compile_candidate, bench.target_text or "", budget=budget,
                                         baseline=baseline, diverse=diverse, enable=enable)
        if outcome.exact:
            (Path(out) / "exact").mkdir(parents=True, exist_ok=True)
            (Path(out) / "exact" / f"{name}.c").write_text(outcome.best_source)
        result = {"function": name, **outcome.summary(), "families": sorted({r["family"] for r in outcome.log}),
                  "exact_path": [r["label"] for r in outcome.log if r.get("exact")],
                  "faults": row["faults"], "seconds": round(time.monotonic() - started, 1)}
    except Exception as error:
        result = {"function": name, "error": f"{type(error).__name__}: {error}"[:400]}
    finally:
        bench.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--labels", action="append", default=[])
    parser.add_argument("--budget", type=int, default=300)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--diverse", action="store_true")
    parser.add_argument("--root", choices=["source", "const_store"], default="source")
    parser.add_argument("--enable", action="store_true", help="enabling roots (regalloc_search enable=True)")
    parser.add_argument("--live-generators", action="store_true", help="without typed_reread/truth_test")
    parser.add_argument("--match", help="every pending function whose name contains this text")
    args = parser.parse_args()
    names = list(args.names)
    if args.match:
        names += sorted(r["function"] for r in json.loads((HERE / "pending.json").read_text()) if args.match in r["function"])
    if args.labels:
        classes = json.loads((HERE / "classes.json").read_text())
        names += [r["function"] for r in classes["rows"] if set(args.labels) & set(r["labels"])]
    args.out.mkdir(parents=True, exist_ok=True)
    rows_path = args.out / "rows.jsonl"
    done = set()
    if rows_path.is_file():
        for line in rows_path.read_text().splitlines():
            try:
                done.add(json.loads(line)["function"])
            except ValueError:
                pass
    todo = [n for n in dict.fromkeys(names) if n not in done]
    with rows_path.open("a") as log, multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for result in pool.imap_unordered(run, [(n, str(args.out), args.budget, args.diverse, args.root, args.enable, args.live_generators) for n in todo]):
            log.write(json.dumps(result) + "\n")
            log.flush()
            print(json.dumps({k: result.get(k) for k in ("function", "exact", "compiles", "best_gradient",
                                                         "exact_path", "error")}), flush=True)


if __name__ == "__main__":
    main()

"""AerialTrick family: does storing the clamp local (store_value_local) clear the structural faults, and does the
register search then finish? Main-tree solver code, isolated bench, nothing written to the campaign.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/progress-census-20260915/probe_aerial.py NAME [--budget 300]
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from eval import regalloc_probe  # noqa: E402
from solver import regalloc_mutations, regalloc_search, regalloc_signature, residual, workspace  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("names", nargs="+")
parser.add_argument("--budget", type=int, default=300)
args = parser.parse_args()
pending = {r["function"]: r for r in json.loads((HERE / "pending.json").read_text())}

for name in args.names:
    row = pending[name]
    source = Path(row["source"]).read_text()
    bench = regalloc_probe.Bench({"name": name, "source": row["source"]})
    try:
        def compile_candidate(candidate, label):
            tag = f"{name}_aer{time.time_ns()}"
            attempt = workspace.score(bench.ws, bench.isolated, tag, candidate, conn=bench.conn, func=name,
                                      strategy="aerial-probe", model="zero-model", run_id=bench.run_id)
            dump = bench.ws / f"{tag}_object_dump_normalized.s"
            text = dump.read_text() if attempt.compiled and dump.is_file() else None
            if bench.target_text is None:
                bench.target_text = (bench.ws / "target_object_dump_normalized.s").read_text()
            return regalloc_search.Compiled(bool(attempt.compiled), bool(attempt.exact), text, attempt.diff or "")

        base = compile_candidate(source, "baseline")
        target = bench.target_text
        base_gradient = regalloc_signature.compare(target, base.dump).gradient
        stores = []
        for label, kind, variant in regalloc_mutations.store_value_locals(source, name):
            result = compile_candidate(variant, label)
            gradient = regalloc_signature.compare(target, result.dump).gradient if result.dump else None
            stores.append({"label": label, "exact": result.exact, "gradient": gradient, "variant": variant})
        best = min((s for s in stores if s["gradient"]), key=lambda s: tuple(s["gradient"]), default=None)
        report = {"function": name, "baseline_gradient": base_gradient,
                  "stores": [(s["label"], s["gradient"], s["exact"]) for s in stores]}
        if best and tuple(best["gradient"]) < tuple(base_gradient):
            outcome = regalloc_search.search(name, best["variant"], compile_candidate, target, budget=args.budget)
            report["search_from_store"] = outcome.summary()
            if outcome.exact:
                (HERE / "aerial").mkdir(exist_ok=True)
                (HERE / "aerial" / f"{name}.exact.c").write_text(outcome.best_source)
        print(json.dumps(report), flush=True)
    finally:
        bench.close()

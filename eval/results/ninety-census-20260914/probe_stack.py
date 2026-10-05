"""Apply solver.stack_layout to the 90+ functions whose diff carries sp-relative differences (offline, zero-model).

    nice /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_stack.py [--kinds stack_only,stack+other] [--workers 2]

Greedy: up to 4 rounds, each keeping the best compiled variant that improves (exact, score); the
next round's proposals are derived from that variant's own diff. Writes stack-probe/<fn>.json and
the best source; summary.json aggregates. Rerun skips functions already written.
"""
import argparse
import json
import multiprocessing
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "stack-probe"


def one(row):
    sys.path.insert(0, str(ROOT))
    from eval import regalloc_probe
    from solver import stack_layout
    target = OUT / f"{row['function']}.json"
    if target.exists():
        return json.loads(target.read_text())
    source = Path(row["source"]).read_text()
    entry = {"function": row["function"], "kind": row["kind"], "stored_score": row["score"]}
    bench = regalloc_probe.Bench({"name": row["function"], "source": row["source"]})
    try:
        base = bench.run(source, "baseline")
        best = (base, source)
        entry.update(baseline_score=base["score"], baseline_deltas=stack_layout.stack_deltas(base.get("_diff", "")))
        for round_index in range(4):
            chosen, tried = None, 0
            for label, kind, variant in stack_layout.variants(best[1], row["function"], best[0].get("_diff", "")):
                result = bench.run(variant, label)
                tried += 1
                if result["compiled"] and (result["exact"], result["score"]) > (
                        (chosen or best)[0]["exact"], (chosen or best)[0]["score"]):
                    chosen = (result, variant, label)
                if result["exact"]:
                    break
            entry.setdefault("rounds", []).append({"tried": tried, "chosen": chosen[2] if chosen else None,
                                                   "score": chosen[0]["score"] if chosen else None})
            if chosen is None:
                break
            best = chosen[:2]
            entry.setdefault("path", []).append(chosen[2])
            if chosen[0]["exact"]:
                break
        entry.update(best_score=best[0]["score"], exact=best[0]["exact"],
                     remaining_deltas=stack_layout.stack_deltas(best[0].get("_diff", "")),
                     outcome="exact" if best[0]["exact"] else "improved" if best[0]["score"] > base["score"] else "no_change")
        if best[1] != source:
            (OUT / f"{row['function']}.c").write_text(best[1])
    except Exception as error:
        entry.update(outcome="error", error=f"{type(error).__name__}: {error}"[:300])
    finally:
        bench.close()
    target.write_text(json.dumps(entry, indent=1))
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kinds", default="stack_only,stack+other")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    kinds = set(args.kinds.split(","))
    sources = {r["function"]: r["source"] for r in json.loads((HERE / "ninety.json").read_text())}
    rows = [{**r, "source": sources[r["function"]]} for r in json.loads((HERE / "stack_only.json").read_text())
            if r["kind"] in kinds]
    rows.sort(key=lambda r: (r["kind"] != "stack_only", r["other_blocks"], r["instructions"] or 0))
    results = []
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for entry in pool.imap_unordered(one, rows):
            results.append(entry)
            print(json.dumps({k: entry.get(k) for k in ("function", "kind", "outcome", "path", "baseline_score", "best_score",
                                                          "error")}), flush=True)
    summary = {"functions": len(results), "outcomes": dict(Counter(r["outcome"] for r in results)),
               "by_kind": {k: dict(Counter(r["outcome"] for r in results if r["kind"] == k)) for k in kinds},
               "first_moves": dict(Counter(r["path"][0].split(":")[0] for r in results if r.get("path")))}
    (OUT / "summary.json").write_text(json.dumps({**summary, "rows": results}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

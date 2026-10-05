"""Zero-compile features for every node of the recorded population searches.

For each compiled, non-exact node: which repair families propose at least one candidate on that node's
own residual (the operation preconditions, measured by running each family's own guard), plus the
residual axes from solver.signals. Edges carry the family applied and the child's outcome. Output is one
JSONL row per node, so the transition model is fitted from observations, never from declared contracts.

Usage (WSL): python3 features.py [--limit N] [--jobs 4]
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
STAGE2 = json.loads((OUT.parent / "population-transfer-20260922/freeze2.json").read_text())
CODE = STAGE2["code_root"]
NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def worlds():
    for rows, arm_dir in ((NATIVE / "rows", "stage1"), (NATIVE / "stage2/rows", "stage2")):
        for path in sorted(rows.glob("*.json")):
            row = json.loads(path.read_text())
            if row.get("world"):
                yield row["function"], row["arm"], row["world"]


def node_features(task):
    function, arm, world_path = task
    sys.path.insert(0, CODE)
    from solver import regalloc_mutations, signals
    world = json.loads(Path(world_path).read_text())["world"]
    out = []
    for node in world["nodes"]:
        v = node["verdict"]
        row = {"function": function, "arm": arm, "id": node["id"], "parent": node["parent"],
               "family": node["family"], "compiled": v["compiled"], "exact": v["exact"], "score": v["score"]}
        if v["compiled"] and not v["exact"]:
            diff = v.get("diff") or ""
            s = signals.analyse(diff, v["score"], False, True)
            row["axes"] = {a: int(getattr(s, a)) for a in AXES}
            fires = {}
            for _label, family, _child in regalloc_mutations.variants(node["source"], function, diff):
                fires[family] = fires.get(family, 0) + 1
            row["fires"] = fires
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    tasks = list(worlds())
    if args.limit:
        tasks = tasks[:args.limit]
    started = time.monotonic()
    target = OUT / ("nodes.jsonl" if not args.limit else "nodes-sample.jsonl")
    done = 0
    with target.open("w") as fh, ProcessPoolExecutor(args.jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
        for future in as_completed([pool.submit(node_features, t) for t in tasks]):
            for row in future.result():
                fh.write(json.dumps(row) + "\n")
            done += 1
            if done % 100 == 0:
                print(json.dumps({"worlds": done, "of": len(tasks), "seconds": round(time.monotonic() - started)}), flush=True)
    print(json.dumps({"worlds": len(tasks), "seconds": round(time.monotonic() - started, 1), "out": target.name}))


if __name__ == "__main__":
    main()

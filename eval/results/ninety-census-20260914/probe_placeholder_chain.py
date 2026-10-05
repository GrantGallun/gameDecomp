"""Placeholder resolution first, then the existing compile_recovery.variants chain, on still-blocked nodes (bench only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_placeholder_chain.py [--workers 1]

Reads placeholder-probe.json; for each still_blocked function, takes each placeholder variant as the
recovery seed, runs compile_recovery.variants, compiles every child. Writes placeholder-chain/<fn>.json.
"""
import argparse
import json
import multiprocessing
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "placeholder-chain2"


def one(function):
    sys.path.insert(0, str(HERE.parents[2]))
    from eval import regalloc_probe
    from solver import compile_recovery, placeholder_declarations as pd, workspace
    target = OUT / f"{function}.json"
    if target.exists():
        return json.loads(target.read_text())
    row = next(r for r in json.loads((HERE / "not_compiled.json").read_text()) if r["function"] == function)
    source = Path(row["source"]).read_text()
    entry = {"function": function, "children": []}
    bench = None
    try:
        bench = regalloc_probe.Bench({"name": function, "source": row["source"]})
        seeds, _ = pd.propose(source, function, pd.header_names(bench.isolated, source))
        for seed_label, seed in seeds:
            attempt = workspace.score(bench.ws, bench.isolated, f"{function}_ph_seed", seed, conn=bench.conn,
                                      func=function, strategy="placeholder-chain", model="zero-model")
            children, reports = compile_recovery.variants(bench.conn, bench.isolated, function, bench.ws, seed, attempt)
            entry.setdefault("stages", {})[seed_label] = sorted({r.get("stage", "?") + ":" + str(r.get("status", "ok"))
                                                                   for r in reports})
            headers = pd.header_names(bench.isolated, seed)
            expanded = []
            for label, child in children:
                expanded.append((label, child))
                # A fresh m2c redraft reintroduces the placeholders; resolve them on the child too.
                expanded += [(f"{label}+{fix}", fixed) for fix, fixed in pd.propose(child, function, headers)[0]]
            for label, child in expanded:
                result = workspace.score(bench.ws, bench.isolated, f"{function}_ph_child", child, conn=bench.conn,
                                         func=function, strategy="placeholder-chain", model="zero-model")
                stderr = result.compiler_stderr or ""
                line = re.search(r"line (\d+): (.*)", stderr)
                entry["children"].append({"seed": seed_label, "label": label, "compiled": result.compiled,
                                          "score": result.score, "exact": result.exact,
                                          "first_error": line.group(2)[:90] if line else None})
    except Exception as error:
        entry["error"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        if bench is not None:
            bench.close()
    compiled = [c for c in entry["children"] if c["compiled"]]
    entry["outcome"] = "error" if "error" in entry else "compiles" if compiled else "still_blocked"
    entry["best"] = max(compiled, key=lambda c: c["score"] or 0, default=None)
    target.write_text(json.dumps(entry, indent=1))
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    blocked = [r["function"] for r in json.loads((HERE / "placeholder-probe.json").read_text()) if r["outcome"] == "still_blocked"]
    results = []
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for entry in pool.imap_unordered(one, blocked):
            results.append(entry)
            errors = Counter(c["first_error"] for c in entry["children"] if not c["compiled"]).most_common(2)
            print(f"{entry['outcome']:13} {entry['function'][:40]:40} best={entry.get('best')} errors={errors} {entry.get('error', '')}",
                  flush=True)
    print(dict(Counter(r["outcome"] for r in results)))


if __name__ == "__main__":
    main()

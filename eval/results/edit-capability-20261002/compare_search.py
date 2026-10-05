"""Paired comparison of two search runs: cases lost, cases gained, compile cost.

    python3 compare_search.py base operators
"""
import json
import statistics
import sys

import run

a_tag, b_tag = sys.argv[1], sys.argv[2]
a = {r["id"]: r for r in map(json.loads, open(run.E / f"search_{a_tag}.jsonl"))}
b = {r["id"]: r for r in map(json.loads, open(run.E / f"search_{b_tag}.jsonl"))}
assert set(a) == set(b), "runs cover different cases"
print(f"{a_tag}: {sum(r['exact'] for r in a.values())}/{len(a)}   {b_tag}: {sum(r['exact'] for r in b.values())}/{len(b)}")
print("lost:", [i for i in a if a[i]["exact"] and not b[i]["exact"]])
print("gained:", [(i, b[i]["winning_edit"]) for i in a if b[i]["exact"] and not a[i]["exact"]])
both = [i for i in a if a[i]["exact"] and b[i]["exact"]]
print(f"compiles to solve, on cases both solve (n={len(both)}): median {statistics.median(a[i]['compiles'] for i in both)}"
      f" vs {statistics.median(b[i]['compiles'] for i in both)}; total {sum(a[i]['compiles'] for i in both)}"
      f" vs {sum(b[i]['compiles'] for i in both)}")
print(f"total compiles all cases: {sum(r['compiles'] for r in a.values())} vs {sum(r['compiles'] for r in b.values())}")

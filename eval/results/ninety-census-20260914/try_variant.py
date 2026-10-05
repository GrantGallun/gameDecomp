"""Compile the stack_layout proposals whose label starts with PREFIX for a 90+ function's stored source (bench only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/try_variant.py FUNCTION PREFIX
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import stack_layout  # noqa: E402

function, prefix = sys.argv[1], sys.argv[2]
entry = json.loads((HERE / "diffs" / f"{function}.json").read_text())
row = next(r for r in json.loads((HERE / "ninety.json").read_text()) if r["function"] == function)
bench = regalloc_probe.Bench({"name": function, "source": row["source"]})
try:
    for label, _kind, text in stack_layout.variants(entry["source_text"], function, entry["diff"]):
        if label.startswith(prefix):
            result = bench.run(text, label)
            print(label, {k: result.get(k) for k in ("compiled", "exact", "score")})
            if not result["exact"]:
                print(result.get("_diff", "")[:1500])
finally:
    bench.close()

"""Compile a source file for a function in an isolated bench and print the oracle diff (no campaign access).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/bench_diff.py FUNCTION SOURCE.c
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval import regalloc_probe  # noqa: E402

function, path = sys.argv[1], Path(sys.argv[2])
bench = regalloc_probe.Bench({"name": function, "source": str(path)})
try:
    result = bench.run(path.read_text(), "bench-diff")
    print({k: result.get(k) for k in ("compiled", "exact", "score")})
    print(result.get("_diff", "")[:4000])
finally:
    bench.close()

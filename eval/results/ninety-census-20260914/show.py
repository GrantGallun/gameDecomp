"""Print a 90+ function's stored source body, target dump and candidate dump side by side (reads diffs/).

    python3 eval/results/ninety-census-20260914/show.py FUNCTION [--full]
"""
import json
import re
import sys
from itertools import zip_longest
from pathlib import Path

HERE = Path(__file__).resolve().parent
entry = json.loads((HERE / "diffs" / f"{sys.argv[1]}.json").read_text())
source = entry["source_text"]
if "--full" not in sys.argv:
    start = re.search(rf"(?m)^[^\n;]*\b{re.escape(sys.argv[1])}\s*\([^;]*$", source)
    source = source[start.start():] if start else source[-3000:]
print(source[:6000])


def code(dump):
    return [l for l in (dump or "").splitlines() if re.match(r"\s*[0-9a-f]+:\s", l) or "\t" in l]


for want, got in zip_longest(code(entry.get("target_dump")), code(entry.get("candidate_dump")), fillvalue=""):
    mark = " " if want.split(":", 1)[-1].strip() == got.split(":", 1)[-1].strip() else "*"
    print(f"{mark} {want[:60]:60} | {got[:60]}")

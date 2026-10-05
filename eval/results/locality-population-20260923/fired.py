"""Did the locality filter fire? Blind-family edges and what the freed compiles went to, narrow vs locality run."""
import collections
import json
from pathlib import Path

HOME = Path.home() / "decomp/experiments"


def families(run):
    c = collections.Counter()
    for path in (HOME / run / "rows").glob("*.json"):
        row = json.loads(path.read_text())
        if not row.get("world"):
            continue
        for n in json.loads(Path(row["world"]).read_text())["world"]["nodes"]:
            if n["parent"] is not None:
                c[n["family"]] += 1
    return c


a, b = families("narrow-population-20260923"), families("locality-population-20260923")
print(f"{'family':24} {'narrow':>7} {'locality':>8} {'delta':>6}")
for fam in sorted(set(a) | set(b), key=lambda f: -(a[f] + b[f]))[:16]:
    print(f"{fam:24} {a[fam]:7} {b[fam]:8} {b[fam] - a[fam]:6}")

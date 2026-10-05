"""Source-side markers per structural class (exploration): which m2c artifacts co-occur with a changed skeleton."""
import collections
import json
import re
from pathlib import Path

import census

MARKERS = {
    "dup_return": re.compile(r"Duplicate return node"),
    "m2c_macro": re.compile(r"\bM2C_[A-Z_]+\("),
    "goto": re.compile(r"\bgoto\b"),
    "switch": re.compile(r"\bswitch\s*\("),
    "loop_label": re.compile(r"^\s*loop_\w+:", re.M),
    "do_while": re.compile(r"\bdo\s*\{"),
    "for": re.compile(r"\bfor\s*\("),
    "while": re.compile(r"\bwhile\s*\("),
}


def main():
    d = json.loads((census.HERE / "census.json").read_text())
    table = collections.defaultdict(collections.Counter)
    per = {}
    for r in d["rows"]:
        row = json.loads((census.E / "rows" / f"{r['function']}--routed.json").read_text())
        world = json.loads(Path(row["world"]).read_text())["world"]
        src = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
        structural = r["class"] != "same-skeleton"
        hits = [k for k, rx in MARKERS.items() if rx.search(src)]
        per[r["function"]] = hits
        table["structural" if structural else "same"]["n"] += 1
        for k in hits:
            table["structural" if structural else "same"][k] += 1
    for k, v in table.items():
        print(k, dict(v))
    (census.HERE / "markers.json").write_text(json.dumps(per, indent=1))


if __name__ == "__main__":
    main()

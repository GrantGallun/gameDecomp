"""Why the offset owners are silent on 99 functions whose diff states the right offset. No compiles.

For each untapped offset node: the (candidate offset -> target offset) pairs from the diff, and how the
candidate's offset is spelled in the source: as a literal (`+ 0x280`, `[0x50]`), as a declared struct field,
or not visibly at all. A literal spelling means the evidence names both the wrong token and its replacement.
"""
import collections
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import signals  # noqa: E402

NODES = HERE.parent / "measured-potential-20260922/nodes.jsonl"
SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]
OWNERS = {"owner:layout", "owner:per_object_layout", "owner:shared_layout", "field_local"}
MEM = re.compile(r"^(\w+)\s+(\w+),(-?(?:0x)?[0-9a-f]+)\((\w+)\)$")


STRICT = "--strict" in sys.argv


def offset_pairs(diff):
    if STRICT:                        # ambiguity-aware alignment: only pairs the aligner is sure of
        from solver import diffrepair
        pairs = diffrepair.aligned_pairs(diff)
    else:
        pairs, _n, _m = signals._pairs(diff)
    out = []
    for target, cand in pairs:
        a, b = MEM.match(target), MEM.match(cand)
        if a and b and a.group(1) == b.group(1) and a.group(3) != b.group(3):
            out.append((int(b.group(3), 0), int(a.group(3), 0)))
    return out


def spelling(source, offset):
    lit = [f"0x{offset:X}", f"0x{offset:x}", str(offset)] if offset else ["0"]
    if any(re.search(rf"[+\[]\s*{re.escape(l)}\b", source) for l in lit if offset):
        return "literal"
    if re.search(rf"/\*\s*(?:offset\s*)?0x{offset:x}\b", source, re.I) or "struct" in source or "typedef" in source:
        return "struct-field"
    return "not-visible"


def main():
    fires = {}
    for line in NODES.open():
        r = json.loads(line)
        if "fires" in r:
            fires[(r["function"], r["arm"], r["id"])] = r["fires"]
    per_function = {}
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world") or row["function"] in per_function:
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            root = world["nodes"][0]
            key = (row["function"], row["arm"], "root")
            if not root["verdict"]["compiled"] or root["verdict"]["exact"] or key not in fires:
                continue
            pairs = offset_pairs(root["verdict"].get("diff") or "")
            if not pairs or any(fires[key].get(o) for o in OWNERS):
                continue
            kinds = collections.Counter(spelling(root["source"], c) for c, _t in pairs)
            per_function[row["function"]] = {"pairs": len(pairs), "spellings": dict(kinds),
                                             "example": [f"0x{c:x}->0x{t:x}" for c, t in pairs[:3]]}
    total = collections.Counter()
    for f in per_function.values():
        total.update(f["spellings"])
    all_literal = [f for f, v in per_function.items() if set(v["spellings"]) == {"literal"}]
    print(json.dumps({"untapped_functions_at_root": len(per_function), "offset_pairs_by_spelling": dict(total),
                      "functions_all_literal": len(all_literal), "examples": all_literal[:8]}, indent=1))
    (HERE / "offset_gap.json").write_text(json.dumps(per_function, indent=1))


if __name__ == "__main__":
    main()

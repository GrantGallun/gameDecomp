"""Print target and rebuilt candidate side by side with block ordinals: python3 show.py FUNCTION"""
import json
import sys
from pathlib import Path

import census


def main(name):
    row = json.loads((census.E / "rows" / f"{name}--routed.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
    tl = (census.E / "ws" / name / row["arm"] / "nonmatchings" / name / "target_object_dump_normalized.s").read_text().splitlines()
    cl = census.apply_diff(tl, node["verdict"]["raw_diff"])
    print(node["source"])
    t, c = census.parse(tl), census.parse(cl)
    print("T", census.skeleton(t))
    print("C", census.skeleton(c))
    for i in range(max(len(tl), len(cl))):
        a = tl[i] if i < len(tl) else ""
        b = cl[i] if i < len(cl) else ""
        print(f"{i*4:4x} {'  ' if a == b else '!!'} {a:40s} {b}")


if __name__ == "__main__":
    main(sys.argv[1])

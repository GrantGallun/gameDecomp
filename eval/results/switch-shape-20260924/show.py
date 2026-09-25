"""Exploration: the switch-signature population functions, their best-node source and control-flow sketch."""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import mine
SW = {'R:extra:sltiu','R:extra:beqz','R:missing:slt','R:opcode:lh/lhu','R:opcode:li/sw'}
a = json.loads((HERE.parent / "draft-reference-mining-20260924/analysis.json").read_text())
rows = [r for r in a["population_rows"] if SW & set(r["features"])]
def best(name):
    row = json.loads((mine.POP / "rows" / f"{name}--routed.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
    return node["source"], node["verdict"], row
if len(sys.argv) > 1:
    src, v, row = best(sys.argv[1])
    print(src); print(v.get("score")); print(v["raw_diff"][:4000])
else:
    for r in rows:
        src, v, _ = best(r["function"])
        print(f'{r["function"]:45s} {r["size"]:6s} {v.get("score")} goto={src.count("goto ")} switch={src.count("switch")} '
              f'sw={sorted(SW & set(r["features"]))}')


def census_all():
    """Exploration: goto/label and register presence across every unsolved best node."""
    import collections
    c = collections.Counter()
    for r in a["population_rows"]:
        src, v, _ = best(r["function"])
        g = src.count("goto ")
        c["with-goto"] += g > 0
        c["gotos"] += g
        c["register-sig"] += bool({"R:opcode:move/sw", "R:opcode:addiu/lw", "R:opcode:nop/sw"} & set(r["features"]))
        c["has-register-kw"] += "register " in src
        c["n"] += 1
    print(dict(c))

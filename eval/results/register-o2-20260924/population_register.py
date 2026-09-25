"""Exploration: population functions with a register signature -- opt level, leaf-ness, register already present."""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "switch-shape-20260924"))
import show
SIG = {"R:opcode:move/sw", "R:opcode:addiu/lw", "R:opcode:nop/sw"}
for r in show.a["population_rows"]:
    if not SIG & set(r["features"]):
        continue
    src, v, row = show.best(r["function"])
    ws = show.mine.POP / "ws" / r["function"] / row["arm"] / "nonmatchings" / r["function"]
    opts = sorted({m for p in ws.glob(".compiler-*.json") if p.name != ".compiler-target.json"
                   for m in re.findall(r'"C_OPT": "([^"]*)"', p.read_text())})
    target = (ws / "target_object_dump_normalized.s").read_text()
    leaf = "jal" not in target
    print(f'{r["function"]:45s} {",".join(opts):4s} {r["size"]:6s} leaf={leaf!s:5s} score={v.get("score")} '
          f'register={"register " in src} sig={sorted(SIG & set(r["features"]))}')

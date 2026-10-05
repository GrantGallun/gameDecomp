"""Fire test of branch_shape.at_inline on every unsolved function carrying m2c's var_at (round-4 v12 bests)."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import branch_shape  # noqa: E402

import fire  # noqa: E402

ROWS = Path.home() / "decomp/experiments/branch-shape-round4-treatment-v11/rows"
for p in sorted(ROWS.glob("*.json")):
    r = json.loads(p.read_text())
    if r.get("exact") or not r.get("world"):
        continue
    w = json.loads(Path(r["world"]).read_text())["world"]
    src = next(x for x in w["nodes"] if x["id"] == r["best_id"])["source"]
    if not re.search(r"\bvar_at\b", src):
        continue
    name = r["function"]
    cands = list(branch_shape.at_inline(src, name))
    if not cands:
        print(f"{name:42s} {r['best_score']:7.3f} no proposal")
        continue
    # all var_at variables at once, then each
    allsrc = src
    for _ in range(4):
        nxt = next(iter(branch_shape.at_inline(allsrc, name)), None)
        if not nxt:
            break
        allsrc = nxt[1]
    for label, cand in [("all", allsrc)] + cands:
        score, dump, log = fire.build(name, cand)
        print(f"{name:42s} {r['best_score']:7.3f} {label:16s} {score}", flush=True)

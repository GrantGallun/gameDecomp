"""Which front: per-function composition of the residual (campaign best attempts), by size. No compiles.

For every compiled-not-exact campaign function: the aligned differing steps of its best attempt (the project's own
classifier), split into REGISTER-only steps (same opcode, operands differ only in registers: field:register),
OPERAND steps (field:offset/immediate/multi/branch: same opcode, other operand differences) and STRUCTURAL steps
(extra/missing/opcode: an instruction added, removed or replaced). Reported by size: medians, the share of functions
whose residual is register-only / register-dominated (>= 80% of steps), and the structural-step counts.

    python3 fronts.py -> fronts.json
"""
import collections
import json
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap  # noqa: E402

HERE = Path(__file__).resolve().parent
H = Path.home() / "decomp"
camp = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
frontier = json.loads((HERE / "frontier.json").read_text())
rows = []
for r in frontier["rows"]:
    diff = camp.execute("select diff_summary from attempts where id=?", (r["attempt"],)).fetchone()[0] or ""
    if not diff.lstrip().startswith(("---", "@@")):
        continue
    steps = [k for k, *_ in mechanism_roadmap.classes(diff, None)]
    if not steps:
        continue
    reg = sum(k == "field:register" for k in steps)
    struct = sum(k.split(":")[0] in ("extra", "missing", "opcode") for k in steps)
    operand = len(steps) - reg - struct
    rows.append({"function": r["function"], "size": r["size"], "insns": r["insns"], "score": r["score"],
                 "steps": len(steps), "register": reg, "operand": operand, "structural": struct})

out = {}
for size in ("small", "medium", "large"):
    rs = [r for r in rows if r["size"] == size]
    if not rs:
        continue
    share = [r["register"] / r["steps"] for r in rs]
    out[size] = {
        "functions": len(rs),
        "median_steps": statistics.median(r["steps"] for r in rs),
        "median_register_share": round(statistics.median(share), 3),
        "median_structural_steps": statistics.median(r["structural"] for r in rs),
        "register_only": sum(r["register"] == r["steps"] for r in rs),
        "register_dominated_ge80": sum(s >= 0.8 for s in share),
        "structural_le2": sum(r["structural"] <= 2 for r in rs),
        "structural_0": sum(r["structural"] == 0 for r in rs),
        "structural_quartiles": statistics.quantiles([r["structural"] for r in rs], n=4),
    }
out["rows"] = rows
(HERE / "fronts.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))

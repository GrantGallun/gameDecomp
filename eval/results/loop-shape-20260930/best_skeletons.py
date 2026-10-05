"""Where is the residual of every unsolved function's best candidate? Skeleton (control-flow shape) vs the rest.

For each unsolved function (census definition), the best compiled attempt in either ledger by site_edits' gradient
(instruction distance, then register distance), and its skeleton distance. If shape were the binding constraint,
most best candidates would still have skeleton distance > 0.
"""
import collections
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from eval import draft_census  # noqa: E402
from solver import signals, skeleton  # noqa: E402

solved = draft_census.solved_by_pipeline()
best = {}
for path in draft_census.LEDGERS:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    for name, diff in db.execute("select f.name, a.diff_summary from attempts a join functions f on f.addr=a.func_addr "
                                 "where a.compiled=1 and coalesce(a.exact,0)=0 and a.diff_summary is not null"):
        if name in solved:
            continue
        g = signals.distances(diff)
        if name not in best or g < best[name][0]:
            best[name] = (g, diff)
rows = []
for name, (g, diff) in best.items():
    t, c = skeleton.skeletons(diff)
    rows.append({"function": name, "insn_distance": g[0], "reg_distance": g[1], "skeleton": skeleton.distance(diff),
                 "branches": len(t)})
(HERE / "best_skeletons.json").write_text(json.dumps(rows, indent=0))
n = len(rows)
zero = [r for r in rows if r["skeleton"] == 0]
print(f"unsolved with a compiled attempt: {n}; best candidate skeleton distance 0: {len(zero)} ({len(zero) / n:.0%})")
for lo, hi in ((0, 10), (10, 30), (30, 100), (100, 10**6)):
    b = [r for r in rows if lo <= r["insn_distance"] < hi]
    print(f"  insn distance {lo}-{hi}: {len(b)}, skeleton 0 in {sum(r['skeleton'] == 0 for r in b)}")
print("skeleton distance histogram:", sorted(collections.Counter(min(r["skeleton"], 10) for r in rows).items()))

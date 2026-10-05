"""Starts: best compiling node across the latest recorded worlds for the 22 functions whose residual is build-class
holes only (group_a) or build-class holes plus register allocation (group_b). Selection: holes_only analysis of
2026-09-25 over the five rows directories below (see README)."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap as mr  # noqa: E402

HERE = Path(__file__).resolve().parent
PT = Path("/mnt/c/Code/gameDecomp/eval/results/population-transfer-20260922")
E = Path.home() / "decomp/experiments"
DIRS = [str(E / d / "rows") for d in ("branch-shape-population-20260924", "at-inline-round4", "counter-loop-round4",
                                      "restart-round3-20260923", "branch-shape-round4-treatment-v11")]
pop = {p["function"]: p for p in json.loads((PT / "population.json").read_text())}
groups = {n: g for g in ("a", "b") for n in (HERE / f"group_{g}.txt").read_text().split()}
best = {}
for fn, world in mr.load(DIRS):
    if fn not in groups:
        continue
    for n in world["nodes"]:
        v = n["verdict"]
        if v["compiled"] and not v["exact"] and (fn not in best or v["score"] > best[fn]["verdict"]["score"]):
            best[fn] = n
starts = []
for fn in sorted(best):
    base = {k: pop[fn][k] for k in ("function", "addr", "insn_count", "source_attempt_id")}
    src = best[fn]["source"]
    starts.append({**base, "group": groups[fn], "start_score": best[fn]["verdict"]["score"], "source": src,
                   "source_sha256": hashlib.sha256(src.encode()).hexdigest()})
(HERE / "starts.json").write_text(json.dumps(starts, indent=1))
print(json.dumps({"starts": len(starts), "missing": sorted(set(groups) - set(best))}))

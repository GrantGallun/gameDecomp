"""Starts for the population run: every function still unsolved in the Sept 23-24 recorded worlds, at its best node."""
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
best, solved = {}, set()
for fn, world in mr.load(DIRS):
    for n in world["nodes"]:
        v = n["verdict"]
        if v["exact"]:
            solved.add(fn)
        elif v["compiled"] and (fn not in best or v["score"] > best[fn]["verdict"]["score"]):
            best[fn] = n
starts = []
for fn in sorted(set(best) - solved):
    src = best[fn]["source"]
    starts.append({**{k: pop[fn][k] for k in ("function", "addr", "insn_count", "source_attempt_id")},
                   "start_score": best[fn]["verdict"]["score"], "source": src,
                   "source_sha256": hashlib.sha256(src.encode()).hexdigest()})
(HERE / "starts-all.json").write_text(json.dumps(starts, indent=1))
print(json.dumps({"starts": len(starts), "solved_in_worlds": len(solved)}))

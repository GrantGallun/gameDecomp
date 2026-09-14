"""Freeze the register-allocation cohorts from the live checkpoint (read-only, via WSL)."""
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908/code")
from eval import campaign_state
RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
pointer = json.loads((RUN / "campaign.json").read_text())
nodes = campaign_state.read(RUN / "campaign.json")["nodes"]
rows = []
for name, n in sorted(nodes.items()):
    f = (n.get("residual") or {}).get("faults") or {}
    other = sum(v for k, v in f.items() if k != "register_allocation")
    if n.get("status") != "pending" or not f.get("register_allocation") or other > 2:
        continue
    source = Path(str(n["source"]).replace("C:\\", "/mnt/c/").replace("\\", "/"))
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != n["source_sha256"]:
        continue
    rows.append({"name": name, "cohort": "regalloc_only" if other == 0 else "regalloc_plus_le2",
                 "source": str(source), "source_sha256": n["source_sha256"], "attempt_id": n["attempt_id"],
                 "address": n["address"], "size": n["size"], "score": n["score"],
                 "instruction_count": n.get("instruction_count"), "faults": f,
                 "semantic_status": (n.get("semantic_validation") or {}).get("status")})
out = {"checkpoint": pointer["commit"], "manifest_sha256": pointer["sha256"], "functions": rows}
Path("/mnt/c/Code/gameDecomp/eval/results/regalloc-20260913/cohort.json").write_text(json.dumps(out, indent=1))
from collections import Counter
print(pointer["commit"], Counter(r["cohort"] for r in rows))

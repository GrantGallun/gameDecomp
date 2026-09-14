"""Freeze a seeded random sample of register-dominant functions with >2 other faults (read-only, via WSL)."""
import hashlib, json, random, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908/code")
from eval import campaign_state
RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
pointer = json.loads((RUN / "campaign.json").read_text())
nodes = campaign_state.read(RUN / "campaign.json")["nodes"]
done = {r["name"] for r in json.loads(Path("/mnt/c/Code/gameDecomp/eval/results/regalloc-20260913/cohort.json").read_text())["functions"]}
pool = []
for name, n in sorted(nodes.items()):
    f = (n.get("residual") or {}).get("faults") or {}
    other = sum(v for k, v in f.items() if k != "register_allocation")
    if n.get("status") != "pending" or name in done or not f.get("register_allocation") or other <= 2:
        continue
    if max(f, key=f.get) != "register_allocation":
        continue
    source = Path(str(n["source"]).replace("C:\\", "/mnt/c/").replace("\\", "/"))
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != n["source_sha256"]:
        continue
    pool.append({"name": name, "cohort": "dominant_sample", "source": str(source), "source_sha256": n["source_sha256"],
                 "attempt_id": n["attempt_id"], "address": n["address"], "size": n["size"], "score": n["score"],
                 "instruction_count": n.get("instruction_count"), "faults": f,
                 "semantic_status": (n.get("semantic_validation") or {}).get("status")})
random.Random(20260913).shuffle(pool)
sample = sorted(pool[:50], key=lambda r: r["name"])
out = {"checkpoint": pointer["commit"], "manifest_sha256": pointer["sha256"], "pool_size": len(pool), "seed": 20260913,
       "selection": "pending, register_allocation is the largest fault class, >2 non-register faults, not in cohort.json",
       "functions": sample}
Path("/mnt/c/Code/gameDecomp/eval/results/regalloc-20260913/dominant-sample.json").write_text(json.dumps(out, indent=1))
print(pointer["commit"], "pool", len(pool), "sample", len(sample))

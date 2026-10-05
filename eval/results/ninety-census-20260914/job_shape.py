"""Print the last job record of a node (read-only): python job_shape.py FUNCTION"""
import json
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
node = state["nodes"][sys.argv[1]]
residual = node.get("residual") or {}
print("residual keys", sorted(residual))
print(json.dumps({k: v for k, v in residual.items() if k not in ("frontend",)}, default=str)[:3000])
for job in node.get("jobs", [])[-2:]:
    print(json.dumps(job, default=str)[:1500])

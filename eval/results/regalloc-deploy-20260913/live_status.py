"""Live check after deployment: regalloc_search jobs completed since the amendment, and their outcomes."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908/code")
from eval import campaign_state  # noqa: E402

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
pointer = json.loads((RUN / "campaign.json").read_text())
state = campaign_state.read(RUN / "campaign.json")
rows = []
for name, node in state["nodes"].items():
    for job in node.get("jobs", []):
        if job.get("profile") == "regalloc_search":
            rows.append((name, node["status"], job.get("status")))
exact = sum(status in ("object_exact", "integrated", "function_exact_pending_integration") for _n, status, _j in rows)
print(json.dumps({"checkpoint": pointer["commit"], "service": json.loads((RUN / "service.json").read_text()).get("status"),
                  "regalloc_jobs": len(rows), "now_exact": exact,
                  "summary_exact": state["summary"].get("object_exact_or_integrated"),
                  "inflight": [j.get("profile", {}).get("name") if isinstance(j.get("profile"), dict) else j.get("profile")
                               for j in state.get("fast_inflight", [])]}))

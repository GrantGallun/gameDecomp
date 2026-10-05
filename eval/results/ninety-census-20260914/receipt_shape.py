"""Show the field shape of one frontend-lane model receipt: python receipt_shape.py [PROFILE]"""
import json
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

profile = sys.argv[1] if len(sys.argv) > 1 else "schema_patch"
state = campaign_state.read(RUN / "campaign.json")
for name, node in state["nodes"].items():
    jobs = [j for j in node.get("jobs", []) if j.get("lane") == "frontend" and j["profile"] == profile]
    if not jobs:
        continue
    receipt = json.loads(Path(jobs[-1]["receipt"]).read_text())
    print(name, "top", sorted(receipt))
    for key, value in receipt.items():
        if isinstance(value, dict):
            print(" ", key, {k: (v if not isinstance(v, (dict, list, str)) or len(str(v)) < 120 else type(v).__name__)
                             for k, v in value.items()})
    break

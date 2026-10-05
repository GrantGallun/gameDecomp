"""Full blocker details and seed/source context for parked operational nodes (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/parked_detail.py [SUBSTRING]
"""
import json
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

needle = sys.argv[1] if len(sys.argv) > 1 else ""
state = campaign_state.read(RUN / "campaign.json")
for name, node in sorted(state["nodes"].items()):
    if node["status"] != "parked":
        continue
    blocker = node.get("blocker") or {}
    text = json.dumps(blocker)
    if needle not in text:
        continue
    print("==", name, "jobs", len(node.get("jobs", [])), "source", node.get("source"), "sha", (node.get("source_sha256") or "")[:10])
    print("   blocker:", text[:700])
    print("   last jobs:", [(j["profile"], j.get("status")) for j in node.get("jobs", [])[-3:]])

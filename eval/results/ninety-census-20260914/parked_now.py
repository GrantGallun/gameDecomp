"""List parked nodes with their blocker, flagging ones not in the gap census (read-only)."""
import json
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

before = {r["function"] for r in json.loads((RUN.parent / "gap-census-20260914/nodes.json").read_text())["parked"]}
state = campaign_state.read(RUN / "campaign.json")
for name, node in sorted(state["nodes"].items()):
    if node["status"] != "parked":
        continue
    blocker = node.get("blocker") or {}
    new = name not in before or node.get("unparked")
    if new:
        jobs = node.get("jobs", [])
        print(name, "| unparked:", bool(node.get("unparked")), "| blocker:", blocker.get("status"),
              str(blocker.get("error") or blocker.get("reason") or "")[:300], "| last job:", jobs[-1]["profile"] if jobs else None)

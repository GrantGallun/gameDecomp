"""Live revalidate outcomes: semantic status before (from the job receipt's input node) vs after (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/pointer-contracts-20260914/revalidate_outcomes.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402
from solver import repair_queue  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
transitions, rows = Counter(), []
for name, node in state["nodes"].items():
    jobs = [j for j in node.get("jobs", []) if j["profile"].startswith("revalidate@")]
    if not jobs:
        continue
    index = node["jobs"].index(jobs[-1])
    after = (node.get("semantic_validation") or {}).get("status")
    lane = repair_queue.lane(node).value
    transitions[(after, lane)] += 1
    rows.append({"function": name, "semantic_now": after, "lane_now": lane, "jobs_after": len(node["jobs"]) - index - 1,
                 "status": node["status"]})
print(json.dumps({"revalidated": len(rows), "now": {f"{a}|{l}": n for (a, l), n in transitions.most_common()}}, indent=1))
for row in rows[:40]:
    print(json.dumps(row))

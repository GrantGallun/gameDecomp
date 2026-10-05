"""Trace scheduling for nodes: job sequence (profile, evidence key, source) and what next_profile picks now (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/schedule_trace.py FUNCTION...
"""
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state, completion_campaign as campaign  # noqa: E402
from solver import repair_queue  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
for name in sys.argv[1:]:
    node = state["nodes"][name]
    key = repair_queue.evidence_key(node)
    print(f"\n{name} lane={repair_queue.lane(node).value} key={key[:10]} src={node['source_sha256'][:10]} faults={(node.get('residual') or {}).get('faults')}")
    for job in node.get("jobs", []):
        print(f"   {job['profile'][:34]:34} status={job.get('status'):10} key={str(job.get('evidence_key'))[:10]} src={str(job.get('source_sha256'))[:10]} lane={job.get('lane')}")
    print("   next:", (repair_queue.next_profile(node, 1, campaign.PROFILES) or {}).get("name"),
          "| model_calls=0:", (repair_queue.next_profile(node, 0, campaign.PROFILES) or {}).get("name"))

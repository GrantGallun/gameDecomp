"""For a non-compiling node: its source's declaration lines, and every compile-recovery stage report in its receipts.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/recovery_reports.py FUNCTION [--source]
"""
import json
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
name = sys.argv[1]
node = state["nodes"][name]
residual = node.get("residual") or {}
print("score", node.get("score"), "signature", (residual.get("compiler_error_signature") or "")[:300])
print("frontend", json.dumps(residual.get("frontend"))[:800])
if "--source" in sys.argv:
    print(Path(node["source"]).read_text()[:6000])


def stages(value, found):
    if isinstance(value, dict):
        if "stage" in value and isinstance(value["stage"], str):
            found.append(value)
        for child in value.values():
            stages(child, found)
    elif isinstance(value, list):
        for child in value:
            stages(child, found)


for job in node.get("jobs", []):
    try:
        receipt = json.loads(Path(job["receipt"]).read_text())
    except (OSError, KeyError, ValueError):
        print(job.get("profile"), job.get("status"), "no receipt")
        continue
    found = []
    stages(receipt, found)
    summary = Counter(f"{r['stage']}:{r.get('status', 'ok')}" for r in found)
    reasons = {r["stage"]: str(r.get("reason"))[:160] for r in found if r.get("reason")}
    result = receipt.get("result") or {}
    print(f"\n{job['profile'][:30]} {job.get('status')} best={((result.get('best_residual') or {}).get('weighted_progress_score'))}"
          f" calls={((receipt.get('performance') or result.get('performance') or {}).get('model_calls'))}")
    print("  ", dict(summary))
    for stage, reason in reasons.items():
        print("   -", stage, reason)

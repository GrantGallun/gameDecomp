"""Do parked nodes' retained frontier/champion sources parse? Read-only query of the campaign attempt log.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/retained_parse.py
"""
import sqlite3
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state, completion_campaign as campaign  # noqa: E402
from solver import repair_context  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
conn = sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True)
for name, node in sorted(state["nodes"].items()):
    error = (node.get("blocker") or {}).get("error", "")
    if node["status"] != "parked" or not ("unbalanced" in error or "ordinary function" in error):
        continue
    verdicts = []
    for row in campaign.retained_candidates(node):
        source = conn.execute("SELECT source_code FROM attempts WHERE id=?", (int(row["attempt_id"]),)).fetchone()
        if source is None:
            verdicts.append((row["attempt_id"], "missing"))
            continue
        try:
            repair_context.definition(source[0], name)
            verdicts.append((row["attempt_id"], "ok"))
        except ValueError as exc:
            verdicts.append((row["attempt_id"], str(exc)))
    print(name, error.split(": ", 1)[-1], verdicts)

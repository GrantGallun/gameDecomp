"""For parked parser-failure nodes: which recent attempts fail repair_context.definition, and their strategies (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/recent_attempt_parse.py
"""
import sqlite3
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402
from solver import repair_context  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
conn = sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True)
strategies = Counter()
for name, node in sorted(state["nodes"].items()):
    error = (node.get("blocker") or {}).get("error", "")
    if node["status"] != "parked" or not ("unbalanced" in error or "ordinary function" in error):
        continue
    rows = conn.execute("SELECT a.id, a.strategy, a.compiled, a.source_code, a.created_at FROM attempts a "
                        "JOIN functions f ON a.func_addr=f.addr WHERE f.name=? ORDER BY a.id DESC LIMIT 40", (name,)).fetchall()
    failing = []
    for attempt_id, strategy, compiled, source, created in rows:
        try:
            repair_context.definition(source or "", name)
        except ValueError as exc:
            failing.append((attempt_id, strategy, compiled, str(exc)[:30]))
            strategies[strategy] += 1
    print(name, "recent", len(rows), "failing", failing[:4])
print(strategies.most_common())

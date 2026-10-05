"""Do parked nodes' own stored sources trip repair_context.definition? (read-only)

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/parse_parked.py
"""
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402
from solver import repair_context  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
for name, node in sorted(state["nodes"].items()):
    error = (node.get("blocker") or {}).get("error", "")
    if node["status"] != "parked" or not ("unbalanced" in error or "ordinary function" in error):
        continue
    source = Path(node["source"]).read_text() if node.get("source") else ""
    try:
        repair_context.definition(source, name)
        verdict = "stored source parses"
    except ValueError as exc:
        verdict = f"stored source fails: {exc}"
    count = source.count(name + "(")
    print(f"{name}: {verdict}; name occurrences {count}; chars {len(source)}")

"""How many jobs re-ran a profile on a source the node had already run that profile on (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/repeat_jobs.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state, completion_campaign as campaign  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
models = {p["name"]: p["model"] for p in campaign.PROFILES}
total, repeats, by_profile, by_model = Counter(), Counter(), Counter(), Counter()
cycling_nodes, rows = 0, []
for name, node in state["nodes"].items():
    seen, node_repeats = set(), 0
    sources = [j.get("source_sha256") for j in node.get("jobs", []) if j.get("lane") == "byte"]
    for job in node.get("jobs", []):
        profile = job["profile"].split("@")[0]
        pair = (job["profile"], job.get("source_sha256"))
        total[profile] += 1
        if job.get("source_sha256") and pair in seen:
            repeats[profile] += 1
            node_repeats += 1
            by_model[models.get(profile, "census")] += 1
        seen.add(pair)
    # A cycle: the byte-lane source returns to one already visited after moving away.
    revisits = sum(1 for i, s in enumerate(sources) if s in sources[:i] and i and sources[i - 1] != s)
    if revisits:
        cycling_nodes += 1
    if node_repeats:
        rows.append({"function": name, "status": node["status"], "score": node.get("score"), "repeats": node_repeats,
                     "revisits": revisits, "jobs": len(node.get("jobs", []))})
report = {"jobs": sum(total.values()), "repeat_jobs": sum(repeats.values()),
          "repeat_share": round(sum(repeats.values()) / max(1, sum(total.values())), 3),
          "repeats_by_profile": dict(repeats.most_common()), "totals_by_profile": dict(total.most_common()),
          "repeats_model_profile": by_model.get(True, 0), "repeats_zero_model_profile": by_model.get(False, 0),
          "nodes_with_repeats": len(rows), "nodes_with_source_cycles": cycling_nodes,
          "pending_with_repeats": sum(r["status"] == "pending" for r in rows),
          "pending_90plus_with_repeats": sum(r["status"] == "pending" and (r["score"] or 0) >= 90 for r in rows)}
(HERE / "repeat_jobs.json").write_text(json.dumps({"report": report, "rows": rows}, indent=1))
print(json.dumps(report, indent=1))

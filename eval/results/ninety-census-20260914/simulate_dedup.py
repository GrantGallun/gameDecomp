"""Simulate the scheduler amendment on the live state (in memory, read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/simulate_dedup.py

Amendment: (1) a zero-model legacy profile already run on the node's CURRENT source counts as
used whatever the evidence key; (2) register_dominant breaks ties toward register allocation.
Reports how next_profile changes across pending nodes.
"""
import json
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state, completion_campaign as campaign  # noqa: E402
from solver import regalloc_search, repair_queue  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
MODEL = {p["name"]: p["model"] for p in campaign.PROFILES}


def tie_dominant(faults, max_other=None):
    faults = faults or {}
    if not faults.get("register_allocation"):
        return False
    other = sum(v for k, v in faults.items() if k != "register_allocation")
    if max_other is not None and other > max_other:
        return False
    return faults["register_allocation"] >= max(faults.values())


def new_next(node, calls):
    original_dominant = regalloc_search.register_dominant
    regalloc_search.register_dominant = tie_dominant
    try:
        spent = {j["profile"] for j in node.get("jobs", [])
                 if j.get("source_sha256") == node.get("source_sha256") and MODEL.get(j["profile"]) is False}
        if not spent:
            return repair_queue.next_profile(node, calls, campaign.PROFILES)
        view = dict(node)
        key = repair_queue.evidence_key(node)
        view["jobs"] = list(node.get("jobs", [])) + [{"profile": p, "evidence_key": key, "source_sha256": node["source_sha256"],
                                                      "synthetic": True} for p in spent]
        return repair_queue.next_profile(view, calls, campaign.PROFILES)
    finally:
        regalloc_search.register_dominant = original_dominant


old_counts, new_counts, transitions = Counter(), Counter(), Counter()
ninety = Counter()
for name, node in state["nodes"].items():
    if node["status"] != "pending":
        continue
    old = (repair_queue.next_profile(node, 1, campaign.PROFILES) or {}).get("name", "NONE").split("@")[0]
    new = (new_next(node, 1) or {}).get("name", "NONE").split("@")[0]
    old_counts[old] += 1
    new_counts[new] += 1
    if old != new:
        transitions[f"{old} -> {new}"] += 1
        if (node.get("score") or 0) >= 90:
            ninety[f"{old} -> {new}"] += 1
print(json.dumps({"old": dict(old_counts.most_common()), "new": dict(new_counts.most_common()),
                  "transitions": dict(transitions.most_common()), "transitions_90plus": dict(ninety.most_common())}, indent=1))

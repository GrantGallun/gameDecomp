"""Remaining failure modes across ALL pending functions, weighted by closeness to done.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/census.py

Read-only over the live checkpoint. Writes census.json (aggregates) and
pending.json (one row per pending function, used by the diff pass).
Closeness tier by total instruction faults: near<=4, close<=12, mid<=40, far.
"""
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402
from solver import repair_queue  # noqa: E402

HERE = Path(__file__).resolve().parent
pointer = json.loads((RUN / "campaign.json").read_text())
state = campaign_state.read(RUN / "campaign.json")


def tier(total):
    return "near<=4" if total <= 4 else "close<=12" if total <= 12 else "mid<=40" if total <= 40 else "far>40"


rows = []
for name, node in state["nodes"].items():
    if node["status"] != "pending":
        continue
    residual = node.get("residual") or {}
    faults = {k: c for k, c in (residual.get("faults") or {}).items() if c}
    compiled = residual.get("compiled")
    frontend = (residual.get("frontend") or {}).get("passed")
    total = sum(faults.values())
    rows.append({
        "function": name, "source": node.get("source"), "attempt_id": node.get("attempt_id"),
        "score": node.get("score"), "instructions": node.get("instruction_count"), "size": node.get("size"),
        "compiled": compiled, "frontend": frontend, "faults": faults, "total_faults": total,
        "non_register": total - faults.get("register_allocation", 0),
        "dominant": ("does_not_compile" if compiled is False else "frontend_rejected" if frontend is False
                     else max(faults, key=faults.get) if faults else "no_instruction_faults"),
        "lane": repair_queue.lane(node).value, "jobs": len(node.get("jobs", [])),
        "semantic": (node.get("semantic_validation") or {}).get("status"),
        "instruction_delta": residual.get("instruction_delta"), "text_length_delta": residual.get("text_length_delta"),
        "boundary": ((node.get("verification") or {}).get("function_boundary") or {}).get("error"),
        "stalled": len(node.get("jobs", [])) >= 4 and len({j.get("source_sha256") for j in node["jobs"][-4:]}) == 1,
    })

compiled = [r for r in rows if r["compiled"] and r["frontend"] is not False]
report = {"checkpoint": pointer["commit"], "pending": len(rows), "compiled_frontend_ok": len(compiled),
          "not_compiled": sum(r["compiled"] is False for r in rows),
          "frontend_rejected": sum(r["compiled"] and r["frontend"] is False for r in rows),
          "stalled": sum(r["stalled"] for r in rows)}
report["tiers"] = dict(Counter(tier(r["total_faults"]) for r in compiled))
by_tier = defaultdict(lambda: {"functions": 0, "dominant": Counter(), "presence": Counter(), "fault_sets": Counter(),
                               "instruction_delta_zero": 0})
for r in compiled:
    t = by_tier[tier(r["total_faults"])]
    t["functions"] += 1
    t["dominant"][r["dominant"]] += 1
    t["instruction_delta_zero"] += int(r["instruction_delta"] == 0)
    for k in r["faults"]:
        t["presence"][k] += 1
    t["fault_sets"]["+".join(sorted(r["faults"])) or "none"] += 1
report["by_tier"] = {k: {"functions": v["functions"], "same_instruction_count": v["instruction_delta_zero"],
                         "dominant": dict(v["dominant"].most_common()), "presence": dict(v["presence"].most_common()),
                         "top_fault_sets": dict(v["fault_sets"].most_common(12))}
                     for k, v in sorted(by_tier.items())}
report["size_by_tier"] = {k: statistics.median([r["instructions"] or 0 for r in compiled if tier(r["total_faults"]) == k])
                          for k in by_tier}
report["not_compiled_size_median"] = statistics.median([r["instructions"] or 0 for r in rows if r["compiled"] is False] or [0])
report["semantic_near_close"] = dict(Counter(r["semantic"] for r in compiled if r["total_faults"] <= 12))
(HERE / "pending.json").write_text(json.dumps(rows, indent=1))
(HERE / "census.json").write_text(json.dumps(report, indent=1))
print(json.dumps(report, indent=1))

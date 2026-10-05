"""Why the campaign stopped gaining: pending residuals, and which lanes each one got on its current source.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/progress-census-20260915/census.py

Read-only over the live checkpoint. Writes census.json (aggregates) and pending.json (one row per pending node).
"""
import json
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402
from solver import regalloc_search, repair_queue  # noqa: E402

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
    total = sum(faults.values())
    jobs = node.get("jobs", [])
    current = jobs[-1].get("source_sha256") if jobs else None
    on_current = [j for j in jobs if j.get("source_sha256") == current]
    rows.append({
        "function": name, "source": node.get("source"), "score": node.get("score"),
        "instructions": node.get("instruction_count"),
        "semantic": (node.get("semantic_validation") or {}).get("status"),
        "stalled": len(jobs) >= 4 and len({j.get("source_sha256") for j in jobs[-4:]}) == 1,
        "non_register": total - faults.get("register_allocation", 0),
        "compiled": residual.get("compiled"), "frontend": (residual.get("frontend") or {}).get("passed"),
        "faults": faults, "total_faults": total, "tier": tier(total),
        "dominant": ("does_not_compile" if residual.get("compiled") is False
                     else "frontend_rejected" if (residual.get("frontend") or {}).get("passed") is False
                     else max(faults, key=faults.get) if faults else "no_instruction_faults"),
        "register_dominant": regalloc_search.register_dominant(faults),
        "register_dominant_le2": regalloc_search.register_dominant(faults, max_other=2),
        "lane": repair_queue.lane(node).value, "jobs": len(jobs),
        "profiles_on_current": sorted({(j.get("profile") or {}).get("name", "?") if isinstance(j.get("profile"), dict)
                                       else str(j.get("profile")) for j in on_current}),
        "jobs_on_current": len(on_current),
        "job_keys": sorted(jobs[-1].keys()) if jobs else [],
    })

report = {"checkpoint": pointer.get("commit"), "pending": len(rows),
          "dominant": dict(Counter(r["dominant"] for r in rows).most_common()),
          "tiers": dict(Counter(r["tier"] for r in rows if r["compiled"])),
          "lanes": dict(Counter(r["lane"] for r in rows).most_common()),
          "register_dominant": sum(r["register_dominant"] for r in rows),
          "register_dominant_le2": sum(r["register_dominant_le2"] for r in rows),
          "register_dominant_never_searched_on_current": sum(
              r["register_dominant"] and not any(p.startswith("regalloc_search") for p in r["profiles_on_current"])
              for r in rows),
          "profiles_on_current": dict(Counter(p for r in rows for p in r["profiles_on_current"]).most_common()),
          "fault_presence": dict(Counter(k for r in rows for k in r["faults"]).most_common()),
          "job_keys_example": rows[0]["job_keys"] if rows else []}
(HERE / "pending.json").write_text(json.dumps(rows, indent=1))
(HERE / "census.json").write_text(json.dumps(report, indent=1))
print(json.dumps(report, indent=1))

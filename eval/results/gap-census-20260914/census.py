"""Census of non-compiling, frontend-rejected, parked and otherwise unreachable campaign functions (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/census.py

Writes census.json and nodes.json (per-node rows for the drill-down).
"""
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402
from solver import repair_queue  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
nodes = state["nodes"]


def error_kinds(text):
    kinds = []
    for pattern, label in ((r"undeclared identifier|undefined symbol|is undefined", "undeclared_identifier"),
                           (r"unknown type name|incomplete type|has incomplete|forward declar", "incomplete_or_unknown_type"),
                           (r"no member named|not a member|member reference", "struct_member"),
                           (r"conflicting types|redefinition|redeclar", "conflicting_declaration"),
                           (r"implicit declaration", "implicit_function"),
                           (r"too (?:few|many) arguments", "call_arity"),
                           (r"incompatible|cannot convert|invalid operands", "type_mismatch"),
                           (r"expected .* before|syntax error|expected expression|unexpected", "syntax"),
                           (r"do-while loop|helper policy", "helper_policy"),
                           (r"M2C_|goto|jump table|switch", "m2c_artifact"),
                           (r"has no text symbols|timeout|Killed", "build_environment")):
        if re.search(pattern, text, re.I):
            kinds.append(label)
    return kinds or ["other"]


rows, report = [], {}
statuses = Counter(n["status"] for n in nodes.values())
report["status"] = dict(statuses)
not_compiled, frontend, parked = [], [], []
for name, node in nodes.items():
    residual = node.get("residual") or {}
    lane = repair_queue.lane(node).value if node["status"] == "pending" else node["status"]
    row = {"function": name, "status": node["status"], "lane": lane, "jobs": len(node.get("jobs", [])),
           "instructions": node.get("instruction_count"), "score": node.get("score")}
    if node["status"] == "parked":
        blocker = node.get("blocker") or {}
        row["blocker"] = blocker.get("status")
        row["blocker_detail"] = json.dumps(blocker)[:400]
        parked.append(row)
    elif node["status"] == "pending" and residual.get("compiled") is False:
        signature = residual.get("compiler_error_signature") or ""
        frontend_text = (residual.get("frontend") or {}).get("diagnostics") or ""
        row["signature"] = signature[:300]
        row["error_kinds"] = error_kinds(signature + "\n" + frontend_text)
        row["profiles"] = Counter(j["profile"].split("@")[0] for j in node.get("jobs", [])).most_common(4)
        not_compiled.append(row)
    elif node["status"] == "pending" and (residual.get("frontend") or {}).get("passed") is False:
        row["diagnostics"] = ((residual.get("frontend") or {}).get("diagnostics") or "")[:300]
        frontend.append(row)
    rows.append(row)

report["not_compiled"] = {"count": len(not_compiled),
                          "error_kinds": dict(Counter(k for r in not_compiled for k in r["error_kinds"]).most_common()),
                          "size_bands": dict(Counter(("<50" if (r["instructions"] or 0) < 50 else "<150" if (r["instructions"] or 0) < 150
                                                      else "<400" if (r["instructions"] or 0) < 400 else "400+") for r in not_compiled)),
                          "jobs_median": sorted(r["jobs"] for r in not_compiled)[len(not_compiled) // 2] if not_compiled else None,
                          "zero_jobs_since_amendment": None}
report["frontend_rejected"] = {"count": len(frontend)}
report["parked"] = {"count": len(parked), "blockers": dict(Counter(r["blocker"] for r in parked).most_common())}

# Functions in the ROM inventory that are not campaign nodes at all.
with sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
    inventory = {row[0]: row[1] for row in conn.execute("SELECT name, insn_count FROM functions")}
missing = sorted(set(inventory) - set(nodes))
report["outside_campaign"] = {"inventory": len(inventory), "campaign_nodes": len(nodes), "missing": len(missing),
                              "examples": missing[:20]}
report["examples"] = {"not_compiled": sorted(not_compiled, key=lambda r: r["instructions"] or 0)[:12],
                      "parked": parked[:12], "frontend": frontend[:6]}
(HERE / "census.json").write_text(json.dumps(report, indent=1))
(HERE / "nodes.json").write_text(json.dumps({"not_compiled": not_compiled, "parked": parked, "frontend": frontend,
                                             "missing": missing}, indent=1))
print(json.dumps({k: v for k, v in report.items() if k != "examples"}, indent=1))

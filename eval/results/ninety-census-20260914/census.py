"""Where the 90+ near-misses and the non-compiling functions stand now (read-only over the live checkpoint).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/census.py

Writes census.json (aggregates), ninety.json (one row per pending node scoring >= 90) and
not_compiled.json (one row per pending node whose stored source does not compile).
"""
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402
from solver import repair_queue  # noqa: E402

pointer = json.loads((RUN / "campaign.json").read_text())
state = campaign_state.read(RUN / "campaign.json")
nodes = state["nodes"]


def job_summary(node):
    jobs = node.get("jobs", [])
    profiles = Counter(j["profile"].split("@")[0] for j in jobs)
    return {"jobs": len(jobs), "profiles": dict(profiles.most_common(8)),
            "job_statuses": dict(Counter(j.get("status") for j in jobs)),
            "last_profiles": [j["profile"].split("@")[0] for j in jobs[-4:]],
            "stalled": len(jobs) >= 4 and len({j.get("source_sha256") for j in jobs[-4:]}) == 1}


ninety, not_compiled = [], []
for name, node in nodes.items():
    if node["status"] != "pending":
        continue
    residual = node.get("residual") or {}
    faults = {k: c for k, c in (residual.get("faults") or {}).items() if c}
    base = {"function": name, "source": node.get("source"), "score": node.get("score"),
            "instructions": node.get("instruction_count"), "lane": repair_queue.lane(node).value,
            "semantic": (node.get("semantic_validation") or {}).get("status"), **job_summary(node)}
    if residual.get("compiled") is False:
        signature = residual.get("compiler_error_signature") or ""
        base["signature"] = signature[:300]
        match = re.search(r"line (\d+): (.*)", signature)
        base["message"] = (match.group(2) if match else signature)[:90]
        try:
            lines = Path(node["source"]).read_text().splitlines()
            number = int(match.group(1)) if match else 0
            base["line_text"] = lines[number - 1].strip()[:160] if 0 < number <= len(lines) else None
        except (OSError, TypeError, KeyError):
            base["line_text"] = None
        not_compiled.append(base)
    elif (node.get("score") or 0) >= 90:
        base.update(faults=faults, total_faults=sum(faults.values()),
                    frontend=(residual.get("frontend") or {}).get("passed"),
                    instruction_delta=residual.get("instruction_delta"),
                    text_length_delta=residual.get("text_length_delta"),
                    fault_set="+".join(sorted(faults)) or "none",
                    boundary=((node.get("verification") or {}).get("function_boundary") or {}).get("error"))
        ninety.append(base)


def message_kind(message):
    for pattern, label in (("Syntax Error", "syntax"), ("undefined", "undefined"), ("redeclaration|redefinition", "redeclaration"),
                           ("operand", "unacceptable_operand"), ("selector|member", "member_selector"),
                           ("incompatible|type", "type"), ("argument", "arguments")):
        if re.search(pattern, message, re.I):
            return label
    return "other"


report = {"checkpoint": pointer.get("commit"), "status": dict(Counter(n["status"] for n in nodes.values()))}
report["ninety"] = {
    "count": len(ninety),
    "score_bands": dict(Counter("99+" if r["score"] >= 99 else "97-99" if r["score"] >= 97 else "95-97" if r["score"] >= 95
                                else "90-95" for r in ninety)),
    "fault_presence": dict(Counter(k for r in ninety for k in r["faults"]).most_common()),
    "fault_sets": dict(Counter(r["fault_set"] for r in ninety).most_common(15)),
    "total_faults_median": statistics.median([r["total_faults"] for r in ninety] or [0]),
    "instruction_delta_zero": sum(r["instruction_delta"] == 0 for r in ninety),
    "lanes": dict(Counter(r["lane"] for r in ninety)),
    "semantic": dict(Counter(r["semantic"] for r in ninety)),
    "stalled": sum(r["stalled"] for r in ninety),
    "jobs_median": statistics.median([r["jobs"] for r in ninety] or [0]),
    "boundary": dict(Counter(r["boundary"] for r in ninety if r["boundary"]).most_common(6)),
}
report["not_compiled"] = {
    "count": len(not_compiled),
    "message_kinds": dict(Counter(message_kind(r["message"]) for r in not_compiled).most_common()),
    "messages": dict(Counter(re.sub(r"'[^']*'|\"[^\"]*\"", "'X'", r["message"])[:60] for r in not_compiled).most_common(15)),
    "jobs_median": statistics.median([r["jobs"] for r in not_compiled] or [0]),
    "stalled": sum(r["stalled"] for r in not_compiled),
}
(HERE / "ninety.json").write_text(json.dumps(sorted(ninety, key=lambda r: -r["score"]), indent=1))
(HERE / "not_compiled.json").write_text(json.dumps(not_compiled, indent=1))
(HERE / "census.json").write_text(json.dumps(report, indent=1))
print(json.dumps(report, indent=1))

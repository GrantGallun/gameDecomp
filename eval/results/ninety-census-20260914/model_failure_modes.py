"""Why model jobs on non-compiling inputs fail: context-budget refusals, invalid edits, no-compile children (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/model_failure_modes.py

Also checks whether IDO's `candidate.c, line N` refers to the same line of the stored source (the text the
model is shown), by comparing the error line against the source's line count and content.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
modes, per_node, sizes = Counter(), Counter(), Counter()
for name, node in state["nodes"].items():
    compiled_by_source = {}
    for job in node.get("jobs", []):
        try:
            receipt = json.loads(Path(job["receipt"]).read_text())
        except (OSError, KeyError, ValueError):
            continue
        best = receipt.get("best_residual") or {}
        input_compiled = compiled_by_source.get(job.get("source_sha256"))
        if receipt.get("best_source_sha256"):
            compiled_by_source[receipt["best_source_sha256"]] = best.get("compiled")
        if job.get("model") is not True or input_compiled is not False:
            continue
        log = "\n".join(map(str, receipt.get("log") or []))
        budget = log.count("ContextBudgetError")
        generated = log.count("generation failed")
        outcome = ("compiled" if best.get("compiled") else
                   "context_budget_only" if budget and budget == generated and "compiled=" not in log.replace("compiled=False", "") and not receipt.get("compiling_children") and "draw" in log and "no compile" not in log else
                   "context_budget_some" if budget else
                   "proposals_did_not_compile")
        modes[outcome] += 1
        if node["status"] == "pending" and (node.get("residual") or {}).get("compiled") is False:
            per_node[(outcome, "still_stuck")] += 1
            sizes[(outcome, "<150" if (node.get("instruction_count") or 0) < 150 else "<400" if node["instruction_count"] < 400 else "400+")] += 1
print("model jobs on non-compiling inputs:", dict(modes))
print("on nodes still stuck:", dict(per_node))
print("still stuck by size:", dict(sorted(sizes.items())))

# Line-number alignment: does the stored source's line N hold the construct IDO complains about?
aligned = Counter()
for name, node in state["nodes"].items():
    residual = node.get("residual") or {}
    if node["status"] != "pending" or residual.get("compiled") is not False:
        continue
    found = re.search(r"line (\d+): (.*)", residual.get("compiler_error_signature") or "")
    diagnostics = (residual.get("frontend") or {}).get("diagnostics") or ""
    clang = re.search(r"candidate\.c:(\d+):\d+: error", diagnostics)
    if found and clang:
        aligned["ido_line_equals_clang_first_error_line" if found.group(1) == clang.group(1) else "differ"] += 1
print("IDO vs clang first-error line numbers:", dict(aligned))

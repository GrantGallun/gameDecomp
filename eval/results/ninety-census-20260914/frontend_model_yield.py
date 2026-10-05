"""How well do jobs on non-compiling / frontend-rejected nodes turn them into compiling candidates? (read-only)

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/frontend_model_yield.py

Every frontend-lane job receipt (fields at top level): model calls, invalid proposals, compiling
children, and whether the job's best candidate compiled / passed the frontend. Grouped by profile,
and for model profiles also by the node's first IDO error message kind before the job.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
by_profile = defaultdict(Counter)
model_samples = []
for name, node in state["nodes"].items():
    compiled_by_source = {}                      # source sha -> compiled, from earlier receipts of this node
    for job in node.get("jobs", []):
        profile = job["profile"].split("@")[0]
        try:
            receipt = json.loads(Path(job["receipt"]).read_text())
        except (OSError, KeyError, ValueError):
            if job.get("lane") == "frontend":
                by_profile[profile]["no_receipt"] += 1
            continue
        best = receipt.get("best_residual") or {}
        input_compiled = compiled_by_source.get(job.get("source_sha256"))
        if receipt.get("best_source_sha256"):
            compiled_by_source[receipt["best_source_sha256"]] = best.get("compiled")
        if job.get("lane") != "frontend" or input_compiled is not False:
            continue                                  # only jobs whose input source is known not to compile
        stats = by_profile[profile]
        stats["jobs"] += 1
        calls = int(receipt.get("calls_attempted") or 0)
        stats["model_calls"] += calls
        stats["jobs_with_model_calls"] += int(calls > 0)
        stats["invalid_proposals"] += len(receipt.get("invalid_proposals") or []) if isinstance(receipt.get("invalid_proposals"), list) else int(receipt.get("invalid_proposals") or 0)
        stats["generations"] += len(receipt.get("generations") or []) if isinstance(receipt.get("generations"), list) else int(receipt.get("generations") or 0)
        stats["compiling_children"] += int(receipt.get("compiling_children") or 0)
        stats["best_compiled"] += int(bool(best.get("compiled")))
        stats["best_compiled_and_frontend_ok"] += int(bool(best.get("compiled")) and (best.get("frontend") or {}).get("passed") is True)
        if calls and len(model_samples) < 400:
            model_samples.append({"function": name, "profile": profile, "calls": calls,
                                  "compiled": best.get("compiled"),
                                  "error": (re.search(r"line \d+: ([^\n]*)", best.get("compiler_error_signature") or "")
                                            or [None, "?"])[1][:60],
                                  "log_tail": [l for l in (receipt.get("log") or [])][-4:]})
report = {p: dict(s) for p, s in sorted(by_profile.items(), key=lambda kv: -kv[1]["jobs"])}
print(json.dumps(report, indent=1))
print("blocked errors after model jobs:",
      dict(Counter(s["error"] for s in model_samples if not s["compiled"]).most_common(10)))
(HERE / "frontend-model-yield.json").write_text(json.dumps({"by_profile": report, "model_samples": model_samples}, indent=1))

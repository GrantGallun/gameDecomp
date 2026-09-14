"""Inspect completed regalloc_search jobs: function, node status, and the search report from the receipt."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908/code")
from eval import campaign_state  # noqa: E402

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
cohort = {r["name"]: r["cohort"] for r in json.loads(Path("/mnt/c/Code/gameDecomp/eval/results/regalloc-20260913/cohort.json").read_text())["functions"]}
state = campaign_state.read(RUN / "campaign.json")
for name, node in state["nodes"].items():
    for job in node.get("jobs", []):
        if job.get("profile") != "regalloc_search":
            continue
        receipt_path = Path(str(job.get("receipt", "")).replace("C:\\", "/mnt/c/").replace("\\", "/"))
        report = None
        for candidate in (*sorted(receipt_path.parent.glob(receipt_path.stem + "*.repair.json")), receipt_path):
            if candidate.is_file():
                data = json.loads(candidate.read_text())
                data = data.get("result", data) if "context_reports" not in data else data
                reports = data.get("context_reports") or []
                report = next((r for r in reports if isinstance(r, dict) and r.get("kind") == "regalloc-search"), None)
                break
        print(json.dumps({"function": name, "cohort": cohort.get(name, "outside"), "node_status": node["status"],
                          "job_status": job.get("status"), "receipt": str(receipt_path)[-90:],
                          "faults": (node.get("residual") or {}).get("faults"),
                          "search": {k: report.get(k) for k in ("exact", "best_label", "compiles", "baseline_gradient", "best_gradient")} if report else None}))

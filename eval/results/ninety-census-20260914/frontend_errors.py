"""Aggregate ALL stored clang frontend errors of the non-compiling nodes (IDO stops at the first).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/frontend_errors.py

Read-only. Writes frontend_errors.json: per-node error list and message-shape counts.
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
ERROR = re.compile(r"^candidate\.c:(\d+):\d+: error: (.*)$", re.M)


def shape(message):
    message = re.sub(r"'[^']*'", "'X'", message)
    return re.sub(r"\d+", "N", message)[:80]


rows, shapes, per_node_shapes = [], Counter(), Counter()
for name, node in sorted(state["nodes"].items()):
    residual = node.get("residual") or {}
    if node["status"] != "pending" or residual.get("compiled") is not False:
        continue
    diagnostics = (residual.get("frontend") or {}).get("diagnostics") or ""
    lines = Path(node["source"]).read_text().splitlines()
    errors = [{"line": int(n), "message": m, "text": lines[int(n) - 1].strip()[:140] if int(n) <= len(lines) else ""}
              for n, m in ERROR.findall(diagnostics)]
    truncated = "too many errors" in diagnostics
    for e in errors:
        shapes[shape(e["message"])] += 1
    for s in {shape(e["message"]) for e in errors}:
        per_node_shapes[s] += 1
    rows.append({"function": name, "error_count": len(errors), "truncated": truncated, "errors": errors,
                 "has_frontend": bool(diagnostics)})

report = {"nodes": len(rows), "with_frontend_diagnostics": sum(r["has_frontend"] for r in rows),
          "error_count_bands": dict(Counter("0" if r["error_count"] == 0 else "1" if r["error_count"] == 1 else "2-4"
                                            if r["error_count"] <= 4 else "5-19" if r["error_count"] < 20 else "20+" for r in rows)),
          "nodes_with_shape": dict(per_node_shapes.most_common(40)), "error_shapes": dict(shapes.most_common(40))}
(HERE / "frontend_errors.json").write_text(json.dumps({"report": report, "rows": rows}, indent=1))
print(json.dumps(report, indent=1))

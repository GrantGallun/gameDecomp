"""Offline descriptive analysis; no new compiler outcomes or policy selection."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.search_replay import Policy, Replay, load_world, merge_worlds, run

OUT = Path(__file__).resolve().parent / "diagnosis-v1"
report = json.loads((OUT / "report.json").read_text())
metrics = {"kind": "retrospective-descriptive-analysis", "training_eligible": False, "runs": [], "sensitivity": []}
for row in report["runs"]:
    if "world" not in row:
        continue
    world = load_world(OUT / row["world"])
    nodes = {n["id"]: n for n in world["nodes"]}
    path, node = [], nodes[row["best_id"]]
    while node is not None:
        path.append({"id": node["id"], "label": node["label"], "score": node["verdict"]["score"]})
        node = nodes.get(node["parent"])
    metrics["runs"].append({"function": row["function"], "policy": row["policy"]["name"], "phase": row["phase"],
        "compiles": row["compiles"], "unique_sources": len({n["source_sha256"] for n in nodes.values()}),
        "compile_refusals": sum(not n["verdict"]["compiled"] for n in nodes.values()),
        "baseline_score": row["baseline_score"], "best_score": row["best_score"], "exact": row["exact"],
        "root_children_tried": sum(n["parent"] == "root" for n in nodes.values()),
        "families": dict(Counter(n["family"] for n in nodes.values())), "best_path": list(reversed(path))})

# These replays use fresh diagnostic worlds only. They describe sensitivity;
# they did not choose the already frozen policy. Missing history stays explicit.
for penalty in (0.25, 0.5, 1.0, 1.18754, 2.0, 2.5, 3.0, 7.307):
    cases = []
    for name in report["diagnostic"]:
        world = merge_worlds([load_world(p) for p in sorted(OUT.glob(f"diagnostic--{name}--*.world.json"))])
        result = run(Replay(world), Policy(f"sensitivity-{penalty:g}", "depth", penalty), report["budget"])
        cases.append({"function": name, **result})
    metrics["sensitivity"].append({"penalty": penalty, "results": cases})
(OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))
for row in metrics["runs"]:
    if row["phase"] == "followup":
        print(row["function"], row["policy"], "unique", row["unique_sources"], "refusals", row["compile_refusals"],
              "baseline", row["baseline_score"], "best", row["best_score"], "root children", row["root_children_tried"])
for row in metrics["sensitivity"]:
    results = row["results"]
    print("sensitivity", row["penalty"], "complete", sum(r["complete"] for r in results),
          "exacts", sum(r["exact"] for r in results), "calls", sum(r["compiles"] for r in results))

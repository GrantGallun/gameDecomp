"""Opportunity census for a `yield` operator on `selection` ranges (exploration, before building it).

A selection range x got colour A although the target's colour D was free when x was coloured; the model's scan
picks the lowest free colour, so in the target something else held A (or a preference pulled x to D). Count, per
selection range: does another range z want A (z's target register == x's actual)? Is z coloured after x in the
candidate? Do both share an outcome class (constrained / unconstrained), which decides raise vs earlier?
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_check  # noqa: E402
import run  # noqa: E402

HERE = Path(__file__).resolve().parent


def main():
    counts, rows = collections.Counter(), []
    for path in sorted((run.E / "restart-round3-20260923/rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        name = row["function"]
        repo = run.workspace(name)
        if not repo:
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        source = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
        report, proc = action_check.trace(repo, name, source)
        if not report or report.get("declined") or not proc:
            continue
        outcome = {d.piece: d.outcome for d in proc.decisions}
        for x in report["ranges"]:
            if x["class"] != "selection":
                continue
            counts["selection"] += 1
            zs = [z for z in report["ranges"] if z["lr"] != x["lr"] and z.get("desired") == x["actual"]]
            rec = {"function": name, "x": x["lr"], "actual": x["actual"], "desired": x["desired"],
                   "model": x.get("selected_by_model"), "x_kind": x["kind"], "z": []}
            for z in zs:
                interferes = z["lr"] in (proc.ranges[x["lr"]].interferes or ())
                rec["z"].append({"lr": z["lr"], "class": z["class"], "kind": z["kind"], "interferes": interferes,
                                 "after_x": (z["order"] is not None and x["order"] is not None and z["order"] > x["order"]),
                                 "same_outcome": outcome.get(z["lr"]) == outcome.get(x["lr"]),
                                 "outcome": outcome.get(z["lr"])})
            if rec["z"]:
                counts["has_z_wanting_actual"] += 1
            if any(z["interferes"] for z in rec["z"]):
                counts["z_interferes"] += 1
            if any(z["interferes"] and z["after_x"] for z in rec["z"]):
                counts["z_interferes_and_after_x"] += 1
            if any(z["interferes"] and z["after_x"] and z["same_outcome"] and z["kind"] == "M" for z in rec["z"]):
                counts["operable_local_z"] += 1
            rows.append(rec)
            print(json.dumps(rec), flush=True)
    out = {"counts": dict(counts), "rows": rows}
    (HERE / "selection_census.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["counts"]))


if __name__ == "__main__":
    main()

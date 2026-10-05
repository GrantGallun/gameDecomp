"""Which allocator failure classes do the unsolved functions' wrong ranges fall in? (start nodes, restart round 3)"""
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
    rows, classes, first = [], collections.Counter(), collections.Counter()
    for path in sorted((run.E / "restart-round3-20260923/rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        name = row["function"]
        repo = run.workspace(name)
        if not repo:
            rows.append({"function": name, "status": "no_workspace"})
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        source = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
        report, _proc = action_check.trace(repo, name, source)
        if not report:
            rows.append({"function": name, "status": "no_trace"})
            continue
        if report.get("declined"):
            rows.append({"function": name, "status": "declined", "why": report["declined"]})
            continue
        wrong = [e for e in report["ranges"] if e["class"] != "ok"]
        for e in wrong:
            classes[e["class"]] += 1
        if wrong:
            first[wrong[0]["class"]] += 1
        rows.append({"function": name, "status": "diagnosed", "non_register": report["non_register"],
                     "wrong": [(e["class"], e["kind"]) for e in wrong], "unattributed": report["unattributed"]})
        print(name, rows[-1]["non_register"], [e["class"] for e in wrong], flush=True)
    status = collections.Counter(r["status"] for r in rows)
    reg_only = [r for r in rows if r["status"] == "diagnosed" and r["non_register"] == 0]
    out = {"status": dict(status), "wrong_range_classes": dict(classes), "first_wrong_class": dict(first),
           "register_only_functions": len(reg_only),
           "register_only_first_class": dict(collections.Counter(r["wrong"][0][0] for r in reg_only if r["wrong"])),
           "rows": rows}
    (HERE / "class_census.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}))


if __name__ == "__main__":
    main()

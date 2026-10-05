"""For `raise` on blocked ranges: did the empty reads lift x's priority past its blocker's? Prints both ranges' save,
block span and adjsave before and after, so a miss can be put on the formula (save), on block growth (span), or on
colouring order rather than priority."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from solver import alloc_inverter  # noqa: E402
import action_check  # noqa: E402
import run  # noqa: E402

HERE = Path(__file__).resolve().parent
FUNCTIONS = {"enqueueSoundEffectWithVolume", "drawRaceSetupSaveChoicePrompts", "drawRaceSetupPlayerCountPrompt"}


def ranges(report, proc, names):
    out = {}
    order = [d.piece for d in proc.decisions if d.outcome != "not_colored"]
    for lr, r in proc.ranges.items():
        if r.kind == "M" and r.offset in names:
            out[names[r.offset]] = {"lr": lr, "adjsave": r.adjsave, "span": alloc_inverter._span(r),
                                    "units": alloc_inverter._units(alloc_inverter._span(r)),
                                    "order": order.index(lr) if lr in order else None, "colour": r.color}
    return out


def main():
    rows = []
    for path in sorted((run.E / "restart-round3-20260923/rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("function") not in FUNCTIONS or row.get("exact"):
            continue
        name = row["function"]
        world = json.loads(Path(row["world"]).read_text())["world"]
        source = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
        repo = run.workspace(name)
        report, proc = action_check.trace(repo, name, source)
        names = alloc_inverter.local_offsets(source, name)
        blocked = {e["lr"]: e for e in report["ranges"] if e["class"] == "blocked"}
        before = ranges(report, proc, names)
        for label, cand in alloc_inverter.propose(source, name, report, proc):
            x = label.split(":")[1].split("+")[0] if label.startswith("raise:") else None
            if not x or before.get(x, {}).get("lr") not in blocked:
                continue
            _, proc2 = action_check.trace(repo, name, cand)
            rec = {"function": name, "label": label, "blockers": blocked[before[x]["lr"]].get("blockers"),
                   "before": before, "after": ranges(None, proc2, names) if proc2 else None}
            rows.append(rec)
            print(json.dumps(rec), flush=True)
    (HERE / "blocked_check.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()

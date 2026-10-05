"""Trace-only action check for `raise`: after the edit, does uopt colour x's range with the register it wanted?

action_check.py needs the attribution diagnosis on the candidate, which declined on all 43 candidates of the fixed
operator (v2). The colour decision is in the trace itself, so read it there: x's range (by frame offset) -> its
colour -> register, compared with the desired register from the start node's diagnosis.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from solver import alloc_inverter, uopt_attribution as ua, uopt_diagnosis, uopt_trace  # noqa: E402
import action_check  # noqa: E402
import run  # noqa: E402

HERE = Path(__file__).resolve().parent


def colours(proc, offset):
    order = [d.piece for d in proc.decisions if d.outcome != "not_colored"]
    out = []
    for lr, r in proc.ranges.items():
        if r.kind == "M" and r.offset == offset:
            num = ua.colour_register(r.color) if r.color and r.color > 0 else None
            out.append({"lr": lr, "register": ua.REGISTER_NAMES[num] if num is not None else None,
                        "adjsave": r.adjsave, "span": alloc_inverter._span(r),
                        "order": order.index(lr) if lr in order else None})
    return out


def v1_report(report):
    """The v1 operator raised on every wrong non-ugen range and at every assignment site: emulate it by labelling
    those ranges `blocked` and treating every site as a counted read."""
    out = dict(report)
    out["ranges"] = [dict(e, **{"class": "blocked"}) if e["class"] not in ("ok", "ugen_temp") else e
                     for e in report["ranges"]]
    return out


V1 = "--v1" in sys.argv
if V1:
    alloc_inverter._read_counts = lambda *_a: True


def main():
    rows = []
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
        names = alloc_inverter.local_offsets(source, name)
        for label, cand in alloc_inverter.propose(source, name, v1_report(report) if V1 else report, proc):
            if not label.startswith("raise:"):
                continue
            x = label.split(":")[1].split("+")[0]
            off = next((o for o, v in names.items() if v == x), None)
            entry = next((e for e in report["ranges"] if e["kind"] == "M" and e["offset"] == off), {})
            texts = uopt_diagnosis.traced_compile(repo / "nonmatchings" / name, repo, cand, run.TRACE_CC, name)
            proc2 = uopt_trace.join(texts["level5"], texts["level6"]).get(name) if texts else None
            rec = {"function": name, "label": label, "desired": entry.get("desired"), "before": colours(proc, off)}
            if proc2 is None:
                rec["after"] = "no_trace"
            else:
                rec["after"] = colours(proc2, off)
                rec["performed"] = any(c["register"] == rec["desired"] for c in rec["after"])
            rows.append(rec)
            print(json.dumps({k: rec[k] for k in ("function", "label", "desired")} |
                             {"performed": rec.get("performed")}), flush=True)
    (HERE / ("action_trace_v1.json" if V1 else "action_trace.json")).write_text(json.dumps(rows, indent=1))
    done = [r for r in rows if "performed" in r]
    print(json.dumps({"candidates": len(rows), "measured": len(done), "performed": sum(r["performed"] for r in done),
                      "functions": len({r["function"] for r in rows}),
                      "functions_performed": len({r["function"] for r in done if r["performed"]})}))


if __name__ == "__main__":
    main()

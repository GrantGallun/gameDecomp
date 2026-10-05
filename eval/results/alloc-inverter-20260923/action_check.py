"""Did each round-0 inverter edit perform its action? Re-trace every candidate and ask whether the targeted variable's
range now holds the register it was diagnosed as wanting, and what happened to the other ranges.

Round 0 is deterministic from the start nodes, so the candidates are regenerated rather than stored.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from solver import alloc_inverter, uopt_diagnosis, uopt_trace  # noqa: E402
import run  # noqa: E402

HERE = Path(__file__).resolve().parent


def trace(repo, name, source):
    texts = uopt_diagnosis.traced_compile(repo / "nonmatchings" / name, repo, source, run.TRACE_CC, name)
    dump = run.build_dump(repo, name, source) if texts else None
    if not texts or not dump:
        return None, None
    target = (repo / "nonmatchings" / name / "target_object_dump_normalized.s").read_text(errors="replace")
    report = uopt_diagnosis.diagnose(target, dump, texts["level5"], texts["level6"], texts["ugen"], name)
    return report, uopt_trace.join(texts["level5"], texts["level6"]).get(name)


def by_offset(report):
    return {e["offset"]: e for e in report.get("ranges", []) if e["kind"] == "M" and e["offset"] is not None}


def main():
    scores = {(l["function"], l["label"]): l for l in json.loads((HERE / "log.json").read_text()) if l["round"] == 0}
    rows = []
    for path in sorted((run.E / "restart-round3-20260923/rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        name = row["function"]
        world = json.loads(Path(row["world"]).read_text())["world"]
        source = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
        repo = run.workspace(name)
        if not repo:
            continue
        report, proc = trace(repo, name, source)
        if not report or report.get("declined") or not proc:
            continue
        names = alloc_inverter.local_offsets(source, name)
        before = by_offset(report)
        for label, cand in alloc_inverter.propose(source, name, report, proc):
            if not label.startswith("raise:"):
                continue
            x = label.split(":")[1].split("+")[0]
            off = next((o for o, v in names.items() if v == x), None)
            want = before.get(off, {}).get("desired")
            after, _ = trace(repo, name, cand)
            rec = {"function": name, "label": label, "variable": x, "desired": want,
                   "actual_before": before.get(off, {}).get("actual"),
                   "wrong_before": report.get("wrong_ranges")}
            logged = scores.get((name, f"inverter:r0:{label}"))
            if logged:
                rec.update(score=logged["score"], parent=logged["parent"])
            if not after or after.get("declined"):
                rec["after"] = "declined" if after else "no_trace"
            else:
                e = by_offset(after).get(off)
                rec.update(actual_after=e["actual"] if e else None, class_after=e["class"] if e else None,
                           wrong_after=after.get("wrong_ranges"),
                           performed=bool(e and e["actual"] == want))
            rows.append(rec)
            print(json.dumps(rec), flush=True)
    (HERE / (sys.argv[1] if len(sys.argv) > 1 else "action_check.json")).write_text(json.dumps(rows, indent=1))
    done = [r for r in rows if "performed" in r]
    print(json.dumps({"candidates": len(rows), "measured": len(done),
                      "performed": sum(r["performed"] for r in done),
                      "performed_and_improved": sum(r["performed"] and r.get("score", 0) > r.get("parent", 1e9) for r in done),
                      "fewer_wrong_ranges": sum((r["wrong_after"] or 0) < (r["wrong_before"] or 0) for r in done)}))


if __name__ == "__main__":
    main()

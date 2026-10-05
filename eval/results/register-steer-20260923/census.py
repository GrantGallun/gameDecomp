"""Why do register-only functions fail? uopt's own diagnosis for each, at its best recorded source. Traced compiles only.

Population: functions of the gated run whose best node's residual has register differences as its ONLY class
(eval.mechanism_roadmap classes). For each: traced compile of the best source in its own isolated workspace,
solver.uopt_diagnosis.diagnose against the target, and for every non-ok range: class, constrained or not,
priority (adjsave), block span, and for blocked ranges the blocker's priority and whether it is constrained.
"""
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap  # noqa: E402
from eval.allocator_rules import span  # noqa: E402
from solver import uopt_diagnosis, uopt_trace  # noqa: E402

HERE = Path(__file__).resolve().parent
RUN = Path.home() / "decomp/experiments/gated-population-20260923"
TRACE_CC = Path.home() / "decomp/tools-src/ido-trace/cc"


def build_dump(ws: Path, repo: Path, name: str, source: str) -> str:
    """Normalized object dump of `source` from the workspace's own build (verdict['dump'] is the score summary)."""
    import subprocess
    (ws / f"{name}.c").write_text(source)
    run = subprocess.run(["bash", "-c", f". {repo}/.venv/bin/activate && bash build.sh {name}.c"], cwd=ws,
                         capture_output=True, text=True, timeout=300)
    dump = ws / f"{name}_object_dump_normalized.s"
    if run.returncode or not dump.exists():
        raise RuntimeError(run.stderr[-400:] or run.stdout[-400:])
    return dump.read_text(errors="replace")


def register_only():
    for path in sorted((RUN / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        compiled = [n for n in world["nodes"] if n["verdict"]["compiled"] and not n["verdict"]["exact"]]
        if not compiled:
            continue
        best = max(compiled, key=lambda n: n["verdict"]["score"])
        classes = {c for c, _s, _l in mechanism_roadmap.classes(best["verdict"].get("diff") or "",
                                                               best["verdict"].get("source_attribution"))}
        if classes == {"field:register"}:
            yield row["function"], best


def main():
    out, tally = [], collections.Counter()
    for name, best in register_only():
        repo = RUN / "ws" / name / "gated"
        ws = repo / "nonmatchings" / name
        texts = uopt_diagnosis.traced_compile(ws, repo, best["source"], TRACE_CC, name)
        if not texts:
            out.append({"function": name, "status": "trace-failed"})
            tally["trace-failed"] += 1
            continue
        candidate_dump = build_dump(ws, repo, name, best["source"])
        report = uopt_diagnosis.diagnose((ws / "target_object_dump_normalized.s").read_text(errors="replace"),
                                         candidate_dump, texts["level5"], texts["level6"], texts["ugen"], name)
        proc = uopt_trace.join(texts["level5"], texts["level6"]).get(name)
        outcome = {d.piece: d.outcome for d in proc.decisions} if proc else {}
        wrong = []
        for e in report.get("ranges", []):
            if e["class"] == "ok":
                continue
            r = proc.ranges[e["lr"]]
            item = {k: e.get(k) for k in ("lr", "class", "kind", "actual", "desired", "adjsave", "order")}
            item.update(outcome=outcome.get(e["lr"]), span=span(r))
            if e["class"] == "blocked":
                item["blockers"] = [{"lr": b, "adjsave": proc.ranges[b].adjsave, "span": span(proc.ranges[b]),
                                     "outcome": outcome.get(b), "kind": proc.ranges[b].kind} for b in e.get("blockers", [])]
            wrong.append(item)
        first = wrong[0] if wrong else None
        tally[report.get("declined") and "declined" or (first["class"] if first else "no-wrong-range")] += 1
        out.append({"function": name, "score": best["verdict"]["score"], "declined": report.get("declined"),
                    "wrong_ranges": wrong, "first": first, "best_source": best["source"], "best_id": best["id"],
                    "trace": texts})
        print(json.dumps({"function": name, "score": best["verdict"]["score"],
                          "first": first and {k: first.get(k) for k in ("class", "kind", "outcome", "adjsave")},
                          "wrong": len(wrong), "declined": report.get("declined")}), flush=True)
    (HERE / "census.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(dict(tally), indent=1))


if __name__ == "__main__":
    main()

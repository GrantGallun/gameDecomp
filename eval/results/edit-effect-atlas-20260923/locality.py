"""L1/L2: do edits that touch a residual's attributed C line improve more often? Recorded edges only."""
import collections
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import alignment, evidence_site  # noqa: E402
from solver.source_attribution import instructions_of, sha  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS = ["population-transfer-20260922/rows", "population-transfer-20260922/stage2/rows",
        "promoted-population-20260923/rows", "gated-population-20260923/rows",
        "index-form-population-20260923/rows", "member-offset-population-20260923/rows",
        "width-population-20260923/rows", "narrow-population-20260923/rows"]


def residual_lines(verdict, source):
    attribution = verdict.get("source_attribution") or {}
    if attribution.get("status") != "verified" or attribution.get("source_sha256") != sha(source):
        return None
    diff = verdict.get("diff") or ""
    where = {r["normalized_line"]: r.get("candidate_line") for r in instructions_of(attribution)}
    stream = evidence_site._candidate_stream_lines(diff)
    lines = set()
    for step in alignment.align_diff(diff).steps:
        c = step.candidate
        if c is None or (step.target is not None and step.target.text == c.text):
            continue
        if c.index < len(stream) and where.get(stream[c.index]):
            lines.add(where[stream[c.index]])
    return lines


def edited_lines(parent, child):
    out = set()
    matcher = difflib.SequenceMatcher(a=parent.split("\n"), b=child.split("\n"), autojunk=False)
    for op, a0, a1, _b0, _b1 in matcher.get_opcodes():
        if op != "equal":
            out.update(range(a0 + 1, max(a1, a0 + 1) + 1))
    return out


def main():
    seen, cells = set(), collections.Counter()
    by_family = collections.defaultdict(collections.Counter)
    home = Path.home() / "decomp/experiments"
    for run in RUNS:
        for path in sorted((home / run).glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world") or not Path(row["world"]).exists():
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            cache = {}
            for n in world["nodes"]:
                if n["parent"] is None:
                    continue
                p = nodes[n["parent"]]
                key = (row["function"], p["source_sha256"], n["source_sha256"])
                if key in seen or not p["verdict"]["compiled"] or p["verdict"]["exact"]:
                    continue
                seen.add(key)
                if p["id"] not in cache:
                    cache[p["id"]] = residual_lines(p["verdict"], p["source"])
                rl = cache[p["id"]]
                if not rl:
                    continue
                touched = bool(edited_lines(p["source"], n["source"]) & rl)
                v = n["verdict"]
                improved = v["exact"] or (v["compiled"] and v["score"] > p["verdict"]["score"])
                cells[(touched, improved)] += 1
                by_family[n["family"]][(touched, improved)] += 1
    def rate(t):
        n = cells[(t, True)] + cells[(t, False)]
        return (round(cells[(t, True)] / n, 4) if n else None), n
    (rt, nt), (rn, nn) = rate(True), rate(False)
    improving = cells[(True, True)] + cells[(False, True)]
    result = {"edges": sum(cells.values()), "touches_residual_line": {"n": nt, "improve_rate": rt},
              "touches_none": {"n": nn, "improve_rate": rn},
              "ratio": round(rt / rn, 2) if rt and rn else None,
              "L1": "supported" if rt and rn and rt >= 2 * rn and nt >= 200 and nn >= 200 else "not supported",
              "L2_improving_kept": round(cells[(True, True)] / max(1, improving), 4),
              "L2_budget_freed": round(nn / max(1, nt + nn), 4),
              "by_family": {f: {"touch_rate": round(c[(True, True)] / max(1, c[(True, True)] + c[(True, False)]), 3),
                                "none_rate": round(c[(False, True)] / max(1, c[(False, True)] + c[(False, False)]), 3),
                                "touch_n": c[(True, True)] + c[(True, False)], "none_n": c[(False, True)] + c[(False, False)]}
                            for f, c in sorted(by_family.items(), key=lambda kv: -sum(kv[1].values()))[:14]}}
    (HERE / "locality.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()

"""Reach of the width rule candidates over the unsolved population (PROTOCOL.md, "Reach"). No compiles.

Each unsolved best node (restart round 3): target dump, candidate dump (recorded diff applied), per-instruction
candidate lines from the node's verified source_attribution. The same alignment and variable code as linemine.py;
the draft type of the event's line variable is read from the node's own source. A function is reached by a rule
when it carries the rule's residual kind on a line whose single primitive variable (or, failing that, whose
assigned variable) has the rule's draft type.

    python3 reach.py   -> reach.json
"""
import collections
import difflib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "branch-layout-20260924"))
import census  # noqa: E402
import linemine  # noqa: E402
from solver.source_attribution import instructions_of  # noqa: E402


def events(name, source, target, cand, lines, stats):
    try:
        dv, df, dlast = linemine.variables(source, name)
    except (StopIteration, ValueError):
        stats["definition not found"] += 1
        return []
    slines = source.splitlines()
    out = []

    def at(i):
        while i >= 0 and lines[i] is None:
            i -= 1
        return lines[i] if i >= 0 else None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, target, cand, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            steps = [((i1 + k if i1 + k < i2 else None), (j1 + k if j1 + k < j2 else None))
                     for k in range(max(i2 - i1, j2 - j1))]
        elif tag == "delete":
            steps = [(ti, None) for ti in range(i1, i2)]
        else:
            steps = [(None, dj) for dj in range(j1, j2)]
        for ti, dj in steps:
            to = linemine.op(target[ti]) if ti is not None else None
            do = linemine.op(cand[dj]) if dj is not None else None
            if not ({to, do} & linemine.WIDTH):
                continue
            kind = (f"field:{to}" if to == do else f"opcode:{to}/{do}") if ti is not None and dj is not None \
                else (f"missing:{to}" if ti is not None else f"extra:{do}")
            line = at(dj if dj is not None else j1 - 1)
            stats["events"] += 1
            if not line or not (df <= line <= dlast):
                stats["event outside the function's lines"] += 1
                continue
            vs, lhs = linemine.line_vars(slines[line - 1], dv)
            v = vs[0] if len(vs) == 1 else lhs
            if not v:
                stats["unpaired"] += 1
                continue
            out.append((kind, dv[v][0], v, line))
    return out


def main():
    analysis = json.loads((HERE / "analysis.json").read_text())
    rules = [r for r in analysis["rules"] if r["changes_type"]]
    stats = collections.Counter()
    reached = collections.defaultdict(list)
    functions = 0
    for path in sorted((census.E / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        name = row["function"]
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        v = node["verdict"]
        tpath = census.E / "ws" / name / row["arm"] / "nonmatchings" / name / "target_object_dump_normalized.s"
        att = v.get("source_attribution") or {}
        if not v.get("compiled") or not v.get("raw_diff") or not tpath.exists() or att.get("status") != "verified":
            stats["no verified attribution or diff"] += 1
            continue
        tl = tpath.read_text().splitlines()
        try:
            cl = census.apply_diff(tl, v["raw_diff"])
        except (ValueError, IndexError):
            stats["diff does not apply"] += 1
            continue
        rows = instructions_of(att)
        lines = [r.get("candidate_line") for r in rows][:len(cl)]
        if len(lines) < len(cl):
            stats["fewer line records than instructions"] += 1
            continue
        target, cand = linemine.mine.mask("\n".join(tl)), linemine.mine.mask("\n".join(cl))
        if target == cand:
            continue
        functions += 1
        evs = events(name, node["source"], target, cand, lines, stats)
        for r in rules:
            hits = [e for e in evs if e[0] == r["kind"] and e[1] == r["draft"]]
            if hits:
                reached[f'{r["kind"]}|{r["draft"]}->{r["ref"]}'].append({"function": name, "sites": hits,
                                                                          "score": v.get("score")})
    union = {f["function"] for fs in reached.values() for f in fs}
    out = {"stats": dict(stats), "functions_with_residual": functions, "reached_any": len(union),
           "per_rule": {k: {"functions": len(v), "list": v} for k, v in sorted(reached.items(), key=lambda kv: -len(kv[1]))}}
    (HERE / "reach.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"stats": out["stats"], "functions_with_residual": functions, "reached_any": len(union),
                      "per_rule": {k: v["functions"] for k, v in out["per_rule"].items()}}, indent=1))


if __name__ == "__main__":
    main()

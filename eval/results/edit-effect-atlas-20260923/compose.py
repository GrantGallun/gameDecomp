"""Make the map actionable: merge line-disjoint improving edits of one parent into a single candidate.

A function with faults on several lines needs several edits, and a depth-limited search applies them one path at
a time. The recorded worlds already hold many single edits that each improved the same parent on different
lines. For every expanded parent P with at least two improving children, merge the children's line hunks that do
not overlap (best first) into one candidate, plus the merge of every pair. Also measures locality: for improving
edges, is the residual on lines the edit did not touch unchanged?
"""
import difflib
import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from locality import residual_lines  # noqa: E402

RUN = Path.home() / "decomp/experiments/locality-population-20260923/rows"


def hunks(parent, child):
    """Line hunks (a0, a1, replacement lines) turning parent into child."""
    a, b = parent.split("\n"), child.split("\n")
    return [(a0, a1, b[b0:b1]) for op, a0, a1, b0, b1 in
            difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes() if op != "equal"]


def overlaps(h1, h2):
    return any(not (x1 < y0 or y1 < x0) for x0, x1, _ in h1 for y0, y1, _ in h2)


def apply(parent, all_hunks):
    lines = parent.split("\n")
    for a0, a1, repl in sorted(all_hunks, key=lambda h: -h[0]):
        lines[a0:a1] = repl
    return "\n".join(lines)


def main():
    probes, local_same, local_total = [], 0, 0
    for path in sorted(RUN.glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        nodes = {n["id"]: n for n in world["nodes"]}
        kids = {}
        for n in world["nodes"]:
            if n["parent"] is not None:
                kids.setdefault(n["parent"], []).append(n)
        seen = set()
        for pid, children in kids.items():
            p = nodes[pid]
            if not p["verdict"]["compiled"] or p["verdict"]["exact"]:
                continue
            good = sorted([c for c in children if c["verdict"]["compiled"] and c["verdict"]["score"] > p["verdict"]["score"]],
                          key=lambda c: -c["verdict"]["score"])
            p_lines = residual_lines(p["verdict"], p["source"]) or set()
            for c in good:                                    # locality: faults off the edited lines unchanged?
                c_lines = residual_lines(c["verdict"], c["source"])
                edited = {a0 + 1 for a0, a1, _ in hunks(p["source"], c["source"])}
                if c_lines is not None and not any(len(r) != 1 for _a0, _a1, r in hunks(p["source"], c["source"])):
                    local_total += 1
                    local_same += (p_lines - edited) <= c_lines | edited
            if len(good) < 2:
                continue
            hs = [(c, hunks(p["source"], c["source"])) for c in good]
            chosen = []
            for c, h in hs:                                   # greedy: best first, skip overlapping hunks
                if all(not overlaps(h, h2) for _c2, h2 in chosen):
                    chosen.append((c, h))
            combos = [chosen] if len(chosen) >= 2 else []
            combos += [[x, y] for x, y in itertools.combinations(chosen[:5], 2)]
            for combo in combos:
                src = apply(p["source"], [hk for _c, h in combo for hk in h])
                if src in seen or src == p["source"]:
                    continue
                seen.add(src)
                probes.append({"function": row["function"], "label": f"compose:{pid}:{len(combo)}", "source": src,
                               "parent_score": p["verdict"]["score"],
                               "best_single": max(c["verdict"]["score"] for c, _ in combo)})
    (HERE / "probes-compose.json").write_text(json.dumps(probes, indent=1))
    print(json.dumps({"locality_improving_edges_checked": local_total,
                      "faults_off_edited_lines_unchanged": local_same,
                      "merged_candidates": len(probes), "functions": len({p["function"] for p in probes})}, indent=1))


if __name__ == "__main__":
    main()

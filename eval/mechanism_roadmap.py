"""What to build, what to refine, and what needs a model: the repair machinery pointing at its own next work.

Run after any campaign whose search worlds are recorded. No compiles. Three views of the same worlds:

BUILD / NEEDS-MODEL  Every residual difference is classified generically from the aligned diff (solver.alignment):
    field:<offset|immediate|symbol|register|branch>, opcode:<a/b>, extra:<op> (candidate only), missing:<op>
    (target only). A class is *stated* when the diff gives a source-expressible fix (a value, symbol, load/store
    type or a removable operator/cast) and *located* when source_attribution names its line. Demand is weighted
    by how close it is to being the last blocker: at each unsolved function's best node, a class present among
    k classes gets weight 1/k, and `sole` counts functions where it is the only one left. Covered = some recorded
    edge reduced it in that function. Stated, located, uncovered demand is a mechanism to build; unstated demand
    is a missing model (allocator, layout), not a rewrite.
REFINE  eval.machinery_card over the same edges, flagged: breaks the build (>=5%), leaves the object unchanged
    (>=50%), or acts without helping (<2% improve), with at least 30 applications. Broken families get refinement
    packets: parent source, bad candidate and the compiler's refusal, the fixture a fix and its test start from.
GATES  Contexts (solver.family_gates: state, recipe, dominant axis) where a family never pays, cross-fitted by
    sha256(function) parity: learned on one half (>=30 applications, 0 exact, improve rate <=1%), accepted only if
    on the held-out half it removes no exact child and at most 1% of the improving children, in both directions.
    Written as proposed-gates.json; install into solver/family_gates.json only after a rerun loses no exact.

    python -m eval.mechanism_roadmap ROWS_DIR [ROWS_DIR...] --out DIR [--gates-from ROWS_DIR]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

from eval import machinery_card
from solver import alignment, evidence_site, family_gates
from solver.source_attribution import instructions_of

MIN_CELL = 30


def classes(diff: str, attribution: dict | None) -> list[tuple[str, bool, bool]]:
    """(class, stated, located) for every differing aligned step."""
    located_lines = set()
    if (attribution or {}).get("status") == "verified":
        located_lines = {r["normalized_line"] for r in instructions_of(attribution) if r.get("candidate_line")}
    stream = evidence_site._candidate_stream_lines(diff)
    out = []
    for step in alignment.align_diff(diff).steps:
        t, c = step.target, step.candidate
        if step.ambiguous or (t is not None and c is not None and t.text == c.text):
            continue
        loc = c is not None and c.index < len(stream) and stream[c.index] in located_lines
        if t is None:
            out.append((f"extra:{c.opcode}", c.opcode in evidence_site.OPERATOR, loc))
        elif c is None:
            out.append((f"missing:{t.opcode}", False, False))
        elif t.opcode != c.opcode:
            stated = t.opcode in evidence_site.LOADSTORE and c.opcode in evidence_site.LOADSTORE
            out.append((f"opcode:{'/'.join(sorted((t.opcode, c.opcode)))}", stated, loc))
        else:
            tf, cf = evidence_site._fields(t.opcode, t.operands), evidence_site._fields(c.opcode, c.operands)
            differ = {k for (k, a), (_k, b) in zip(tf, cf) if a != b} if len(tf) == len(cf) else {"shape"}
            kind = differ.pop() if len(differ) == 1 else "multi"
            out.append((f"field:{kind}", kind in ("offset", "immediate", "symbol"), loc))
    return out


def load(dirs):
    for d in dirs:
        for function, world in machinery_card._worlds([d]):
            yield function, world


def fold(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest(), 16) % 2


def reached(verdict: dict) -> bool:
    """Object exact, or ROM-certified function bytes (solver.function_boundary) awaiting only integration.

    Counting only object exactness listed certified functions as open holes: returnToCourseSelectModeMenu (97.7,
    31 `field:symbol` steps that are aliases of the same addresses), osSpTaskStartGo and rmonPrintf were all
    `function_exact_pending_integration` (restored-holes-20260925).
    """
    boundary = ((verdict.get("verification") or {}).get("function_boundary") or {})
    return bool(verdict.get("exact")) or (bool(verdict.get("compiled")) and boundary.get("function_exact") is True)


def improved(parent, child) -> bool:
    return child["exact"] or (child["compiled"] and child["score"] > parent["score"])


def demand(worlds) -> list[dict]:
    rows = collections.defaultdict(lambda: {"functions": set(), "covered": set(), "weight": 0.0, "sole": 0,
                                            "stated": 0, "located": 0, "instances": 0})
    for function, world in worlds:
        nodes = {n["id"]: n for n in world["nodes"]}
        if any(reached(n["verdict"]) for n in nodes.values()):
            continue
        per_node = {}
        for n in world["nodes"]:
            v = n["verdict"]
            if v["compiled"] and not v["exact"]:
                found = classes(v.get("diff") or "", v.get("source_attribution"))
                per_node[n["id"]] = collections.Counter(c for c, _s, _l in found)
                for c, stated, loc in found:
                    r = rows[c]
                    r["functions"].add(function)
                    r["instances"] += 1
                    r["stated"] += stated
                    r["located"] += loc
        for n in world["nodes"]:
            if n["parent"] in per_node and n["verdict"]["compiled"]:
                after = per_node.get(n["id"], collections.Counter())
                for c, k in per_node[n["parent"]].items():
                    if after[c] < k:
                        rows[c]["covered"].add(function)
        if per_node:
            best = max((n for n in world["nodes"] if n["id"] in per_node), key=lambda n: n["verdict"]["score"])
            present = set(per_node[best["id"]])
            for c in present:
                rows[c]["weight"] += 1 / len(present)
                rows[c]["sole"] += len(present) == 1
    out = []
    for c, r in rows.items():
        stated = r["stated"] / r["instances"] > 0.5
        out.append({"class": c, "functions": len(r["functions"]), "uncovered": len(r["functions"] - r["covered"]),
                    "blocker_weight": round(r["weight"], 2), "sole_blocker": r["sole"], "stated": stated,
                    "located": round(r["located"] / r["instances"], 3)})
    return sorted(out, key=lambda r: (-r["blocker_weight"], -r["uncovered"]))


def refine(worlds, out: Path) -> list[dict]:
    worlds = list(worlds)
    card = machinery_card.card(worlds)
    flagged = []
    for fam, t in card.items():
        if t["edges"] < MIN_CELL:
            continue
        why = [w for w, hit in (("breaks the build", t["broke"] >= 0.05),
                                ("object unchanged", t["no_op"] >= 0.5),
                                ("acts without helping", t["acted"] >= 0.2 and t["of_acted_improved"] < 0.02)) if hit]
        if why:
            flagged.append({"family": fam, "why": why, **{k: t[k] for k in ("edges", "functions", "broke", "no_op",
                                                                          "acted", "of_acted_improved", "exact")}})
    packets = collections.defaultdict(list)
    broken = {f["family"] for f in flagged if "breaks the build" in f["why"]}
    for function, world in worlds:
        nodes = {n["id"]: n for n in world["nodes"]}
        for n in world["nodes"]:
            if n["family"] in broken and n["parent"] is not None and not n["verdict"]["compiled"] \
                    and len(packets[n["family"]]) < 5:
                err = next((l for l in (n["verdict"].get("stderr") or "").splitlines() if "rror" in l), "")
                packets[n["family"]].append({"function": function, "label": n["label"], "refusal": err,
                                             "parent_source": nodes[n["parent"]]["source"], "bad_candidate": n["source"]})
    folder = out / "refine-packets"
    folder.mkdir(parents=True, exist_ok=True)
    for fam, items in packets.items():
        (folder / f"{fam.replace(':', '_')}.json").write_text(json.dumps(items, indent=1))
    return flagged


def gates(worlds) -> dict:
    cells = [collections.defaultdict(lambda: [0, 0, 0, set()]) for _ in (0, 1)]   # n, improving, exact, functions
    for function, world in worlds:
        nodes = {n["id"]: n for n in world["nodes"]}
        for n in world["nodes"]:
            if n["parent"] is None:
                continue
            pv = nodes[n["parent"]]["verdict"]
            ctx = family_gates.context(pv.get("diff") or "", pv)
            for feature, value in ctx.items():
                cell = cells[fold(function)][(n["family"], feature, value)]
                cell[0] += 1
                cell[1] += improved(pv, n["verdict"])
                cell[2] += n["verdict"]["exact"]
                cell[3].add(function)
    accepted, rejected = [], []
    for key in set(cells[0]) | set(cells[1]):
        a, b = cells[0].get(key), cells[1].get(key)
        ok = True
        for train, held in ((a, b), (b, a)):
            if not train or train[0] < MIN_CELL or train[2] or train[1] > 0.01 * train[0]:
                ok = False
                break
            if held and (held[2] or held[1] > max(1, 0.01 * held[0])):
                ok = False
                break
        if not (a and b and a[0] + b[0] >= 2 * MIN_CELL):
            continue
        row = {"family": key[0], "feature": key[1], "value": key[2], "applications": a[0] + b[0],
               "improving": a[1] + b[1], "exact": a[2] + b[2], "functions": len(a[3] | b[3])}
        (accepted if ok else rejected).append(row)
    accepted.sort(key=lambda r: -r["applications"])
    return {"gates": accepted, "min_cell": MIN_CELL, "rule": "cross-fitted by function parity; see eval.mechanism_roadmap",
            "near_misses": sorted((r for r in rejected if not r["exact"] and r["improving"] <= 0.03 * r["applications"]),
                                  key=lambda r: -r["applications"])[:10]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rows", nargs="+", help="rows directories (or world files) of recorded searches")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--gates-from", nargs="*", help="learn gates only from these (runs of the current code)")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    need = demand(load(args.rows))
    flagged = refine(load(args.rows), args.out)
    proposal = gates(load(args.gates_from or args.rows))
    build = [r for r in need if r["stated"] and r["located"] >= 0.5 and r["uncovered"]][:15]
    model = [r for r in need if not r["stated"]][:10]
    (args.out / "roadmap.json").write_text(json.dumps({"build": build, "needs_model": model, "refine": flagged,
                                                       "demand": need}, indent=1))
    (args.out / "proposed-gates.json").write_text(json.dumps(proposal, indent=1))
    lines = ["# Mechanism roadmap", "", "## Build (diff states the fix, source map locates it, nothing covers it)", "",
             "| class | blocker weight | sole blocker | functions | uncovered | located |", "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| `{r['class']}` | {r['blocker_weight']} | {r['sole_blocker']} | {r['functions']} | {r['uncovered']} | {r['located']} |" for r in build]
    lines += ["", "## Needs a model (the diff does not state a source fix)", "",
              "| class | blocker weight | sole blocker | functions |", "|---|---:|---:|---:|"]
    lines += [f"| `{r['class']}` | {r['blocker_weight']} | {r['sole_blocker']} | {r['functions']} |" for r in model]
    lines += ["", "## Refine", "", "| family | why | applications | broke | no-op | improve when acting | exact |",
              "|---|---|---:|---:|---:|---:|---:|"]
    lines += [f"| `{f['family']}` | {', '.join(f['why'])} | {f['edges']} | {f['broke']} | {f['no_op']} | {f['of_acted_improved']} | {f['exact']} |"
              for f in flagged]
    lines += ["", "## Proposed gates (cross-fitted; install only after a rerun loses no exact)", "",
              "| family | skip when | applications | improving | functions |", "|---|---|---:|---:|---:|"]
    lines += [f"| `{g['family']}` | {g['feature']} = {g['value']} | {g['applications']} | {g['improving']} | {g['functions']} |"
              for g in proposal["gates"]]
    (args.out / "ROADMAP.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

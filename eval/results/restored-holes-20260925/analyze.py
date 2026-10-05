"""Coverage, not impact: per function and residual class, is it gone at the arm's end state, and which arm removed it.

Start classes come from starts.json (the Sept 23-24 best node). End = exact, or the search's best node. regalloc_search
end states are reported beside it (exact or register gradient) since its compiles are not world nodes.
    python3 analyze.py  -> analysis.json, prints the holes table
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap as mr  # noqa: E402

HERE = Path(__file__).resolve().parent
E = Path.home() / "decomp/experiments"
starts = {s["function"]: s for s in json.loads((HERE / "starts.json").read_text())}
pop_worlds = {}
for fn, world in mr.load([str(E / d / "rows") for d in ("branch-shape-population-20260924", "at-inline-round4",
                          "counter-loop-round4", "restart-round3-20260923", "branch-shape-round4-treatment-v11")]):
    if fn in starts:
        for n in world["nodes"]:
            if n["source_sha256"] == starts[fn]["source_sha256"] and n["verdict"]["compiled"]:
                pop_worlds[fn] = n["verdict"]


def cls(verdict):
    return collections.Counter(c for c, _s, _l in mr.classes(verdict.get("diff") or "", verdict.get("source_attribution")))


out, table = {}, collections.defaultdict(lambda: collections.Counter())
for arm in ("control", "restored"):
    worlds = dict(mr.load([str(E / f"restored-holes-{arm}" / "rows")]))
    for fn, s in starts.items():
        row = json.loads((E / f"restored-holes-{arm}" / "rows" / f"{fn}--{arm}.json").read_text())
        start = cls(pop_worlds[fn])
        if row.get("exact"):
            end, how = collections.Counter(), ("regalloc_search" if row.get("regalloc", {}).get("exact")
                                              else "+".join(row["best_path_families"]) or "baseline")
        else:
            best = next(n for n in worlds[fn]["nodes"] if n["id"] == row["best_id"])
            end, how = cls(best["verdict"]), "+".join(row["best_path_families"])
        out.setdefault(fn, {"group": s["group"], "start": dict(start)})[arm] = {
            "exact": bool(row.get("exact")), "end": dict(end), "path": how, "regalloc": row.get("regalloc"),
            "best_score": row.get("best_score")}
        for c in start:
            table[c][f"{arm}_closed"] += end[c] == 0
            table[c]["functions"] += arm == "control"
(HERE / "analysis.json").write_text(json.dumps({"functions": out, "classes": table}, indent=1))
print(f"{'class':24} fns  control-closed  restored-closed")
for c, t in sorted(table.items(), key=lambda kv: -kv[1]["functions"]):
    print(f"{c:24} {t['functions']:3}  {t['control_closed']:14}  {t['restored_closed']:15}")
for arm in ("control", "restored"):
    ex = sorted(f for f, v in out.items() if v[arm]["exact"])
    print(arm, "exact", len(ex), [(f, out[f][arm]["path"]) for f in ex])

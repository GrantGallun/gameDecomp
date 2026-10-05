"""Population coverage across the three paired arms (restored v15, pure_inline v16, local_webs v17), same starts.

Per arm: exact functions; per residual class present at the start, in how many functions the arm's end state no
longer has it (closed; a ROM-certified node counts as closed) and in how many it shrank (reduced). Paired check: exacts lost between consecutive arms.
    python3 analyze_pop.py -> analysis-pop.json
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap as mr  # noqa: E402

HERE = Path(__file__).resolve().parent
E = Path.home() / "decomp/experiments"
ARMS = [a for a in ("restored", "pure_inline", "local_webs") if (E / f"restored-holes-pop-{a}" / "rows").is_dir()]
starts = {s["function"]: s for s in json.loads((HERE / "starts-all.json").read_text())}


def cls(verdict):
    return collections.Counter(c for c, _s, _l in mr.classes(verdict.get("diff") or "", verdict.get("source_attribution")))


start_cls, out = {}, {"arms": {}, "classes": collections.defaultdict(dict)}
for arm in ARMS:
    rows_dir = E / f"restored-holes-pop-{arm}" / "rows"
    worlds = dict(mr.load([str(rows_dir)]))
    reached_only = []
    exact, paths, closed, reduced, present = [], collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    for fn in starts:
        path = rows_dir / f"{fn}--{arm}.json"
        if not path.exists():
            continue
        row = json.loads(path.read_text())
        if row.get("status") != "ok":
            continue
        nodes = {n["id"]: n for n in worlds[fn]["nodes"]}
        if fn not in start_cls and nodes["root"]["verdict"]["compiled"]:
            start_cls[fn] = cls(nodes["root"]["verdict"])
        start = start_cls.get(fn, collections.Counter())
        certified = [n for n in nodes.values() if mr.reached(n["verdict"]) and not n["verdict"]["exact"]]
        if certified and not row.get("exact"):
            reached_only.append(fn)
        end = collections.Counter() if row.get("exact") or certified else cls(nodes[row["best_id"]]["verdict"])
        if row.get("exact"):
            exact.append(fn)
            paths["+".join(row["best_path_families"])] += 1
        for c, k in start.items():
            present[c] += 1
            closed[c] += end[c] == 0
            reduced[c] += end[c] < k
    out["arms"][arm] = {"exact": sorted(exact), "exact_paths": dict(paths), "certified_only": sorted(reached_only)}
    for c in present:
        out["classes"][c][arm] = {"functions": present[c], "closed": closed[c], "reduced": reduced[c]}
for a, b in zip(ARMS, ARMS[1:]):
    lost = sorted(set(out["arms"][a]["exact"]) - set(out["arms"][b]["exact"]))
    gained = sorted(set(out["arms"][b]["exact"]) - set(out["arms"][a]["exact"]))
    out[f"{a}->{b}"] = {"lost": lost, "gained": gained}
(HERE / "analysis-pop.json").write_text(json.dumps(out, indent=1))
for arm in ARMS:
    print(arm, "exact", len(out["arms"][arm]["exact"]), "certified-only", len(out["arms"][arm]["certified_only"]),
          out["arms"][arm]["exact_paths"])
for k in [k for k in out if "->" in k]:
    print(k, out[k])
print(f"{'class':22}" + "".join(f"{a:>26}" for a in ARMS))
top = sorted(out["classes"].items(), key=lambda kv: -max(v["functions"] for v in kv[1].values()))[:25]
for c, v in top:
    print(f"{c:22}" + "".join(f"{str((v[a]['functions'], v[a]['closed'], v[a]['reduced'])) if a in v else '-':>26}" for a in ARMS))

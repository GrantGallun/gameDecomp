"""The 244 unsolved functions within 10 instructions of the target: residual classes and one-sided instruction deltas."""
import collections, json, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from eval import draft_census
from solver import diffrepair, residual_classes, rule_miner, signals

rows = {r["function"]: r for r in json.loads((HERE / "best_skeletons.json").read_text())}
LO, HI = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 10)
near = {n for n, r in rows.items() if LO <= r["insn_distance"] < HI}
OUTNAME = "near_miss.json" if (LO, HI) == (0, 10) else f"band_{LO}_{HI}.json"
solved = draft_census.solved_by_pipeline()
best = {}
for path in draft_census.LEDGERS:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    for name, aid, diff in db.execute("select f.name, a.id, a.diff_summary from attempts a join functions f on "
                                      "f.addr=a.func_addr where a.compiled=1 and coalesce(a.exact,0)=0 and a.diff_summary is not null"):
        if name not in near:
            continue
        g = signals.distances(diff)
        if name not in best or g < best[name][0]:
            best[name] = (g, str(path), aid, diff)
classes, feats, keys = collections.Counter(), collections.Counter(), collections.Counter()
out = []
for name, (g, path, aid, diff) in best.items():
    c = residual_classes.counts(diff)
    k = None
    f = rule_miner.features(diff)
    for x, v in c.items():
        if v:
            classes[x] += 1
    only = tuple(sorted(x for x, v in c.items() if v))
    keys[only] += 1
    feats.update({x: 1 for x in f if not x.startswith("class:")})
    out.append({"function": name, "ledger": path, "attempt_id": aid, "gradient": g, "classes": {x: v for x, v in c.items() if v}})
(HERE / OUTNAME).write_text(json.dumps(sorted(out, key=lambda r: r["gradient"]), indent=1))
print("functions", len(best))
print("class presence:", classes.most_common())
print("class combinations:", keys.most_common(15))
print("top one-sided instruction features:", feats.most_common(40))

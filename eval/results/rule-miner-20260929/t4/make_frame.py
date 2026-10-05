"""T4 frame: 50 from the development pool (held-out pool minus the sealed 50), seed 4."""
import json, random, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
R = HERE.parents[1]
sys.path.insert(0, str(R / "heldout50-20260929"))
sealed = {r["name"] for r in json.loads((R / "heldout50-20260929" / "frame.json").read_text())}
excl = set(json.loads((R / "chain-vs-search-20260929" / "frame_names.json").read_text()))
excl |= {r["name"] for r in json.loads((R / "composed-edits-20260929" / "frame-A.json").read_text())}
excl |= {r["name"] for r in json.loads((R / "composed-edits-20260929" / "frame-B.json").read_text())}
L = {"campaign": "/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite", "kb": "/home/grant/decomp/kb-sbk1.sqlite"}
exact, best = set(), {}
for p in L.values():
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
for led, p in L.items():
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    for n, sc, i, ic in db.execute("select f.name, max(a.score), a.id, f.insn_count from attempts a join functions f "
                                   "on f.addr=a.func_addr where a.compiled=1 and a.exact=0 group by f.name"):
        if n not in exact and n not in excl and n not in sealed and (n not in best or sc > best[n]["score"]):
            best[n] = {"name": n, "ledger": led, "attempt_id": i, "score": sc, "size": ic,
                       "band": "small" if ic < 50 else "medium" if ic < 150 else "large+"}
pool = sorted(best)
frame = [best[n] for n in random.Random(4).sample(pool, 50)]
(HERE / "frame.json").write_text(json.dumps(frame, indent=1))
print("dev pool", len(pool), "frame", len(frame), "overlap with sealed", len({r['name'] for r in frame} & sealed))

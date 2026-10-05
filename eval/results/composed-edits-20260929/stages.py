"""Class-key arm, B207: the first class still non-zero at each function's best state, and budget use."""
import collections, json, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import residual_classes as rc
ARM = sys.argv[1] if len(sys.argv) > 1 else "class"
db = sqlite3.connect(f"file:/home/grant/decomp/runs/composed-edits-20260929/{ARM}.sqlite?mode=ro", uri=True)
addr = {n: a for a, n in db.execute("select addr, name from functions")}
rows = {json.loads(l)["function"]: json.loads(l) for l in (HERE / f"{ARM}-B207.jsonl").read_text().splitlines()}
stage = collections.Counter(); stage_band = collections.defaultdict(collections.Counter); base_stage = collections.Counter()
moved = collections.Counter(); spent = collections.Counter(); cross = collections.Counter()
for n, r in rows.items():
    atts = db.execute("select strategy, compiled, diff_summary from attempts where func_addr=? order by id", (addr[n],)).fetchall()
    keys = [(s, tuple(rc.counts(d or "")[k] for k in rc.CLASSES)) for s, c, d in atts if c]
    if not keys: stage["never compiled"] += 1; continue
    base = next((k for s, k in keys if s.endswith(":baseline")), keys[0][1])
    best = min(k for _s, k in keys)
    first = next((rc.CLASSES[i] for i, v in enumerate(best) if v), "none")
    bfirst = next((rc.CLASSES[i] for i, v in enumerate(base) if v), "none")
    stage[first] += 1; stage_band[r["band"]][first] += 1; base_stage[bfirst] += 1
    moved["best stage later than baseline stage" if rc.CLASSES.index(first) > rc.CLASSES.index(bfirst) else "same stage"] += 1
    why = "budget exhausted" if (r.get("compiles") or 0) >= 48 else "no improving child"
    spent[why] += 1
    cross[(first, why)] += 1
print("first non-zero class at baseline:", base_stage.most_common())
print("first non-zero class at best:    ", stage.most_common())
for b, c in stage_band.items(): print("  ", b, c.most_common())
print(dict(moved)); print(dict(spent))

for (st, why), v in sorted(cross.items()): print(f"  {st:14s} {why:20s} {v}")

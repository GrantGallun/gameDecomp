"""Paired comparison of two recorded arms by best site_edits.gradient; one-sided sign test (new vs old).

    python compare.py NEW_DB NEW_JSONL OLD_DB OLD_JSONL
"""
import json, math, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from solver import signals


def best(db_path, names):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    addr = {n: a for a, n in db.execute("select addr, name from functions")}
    out = {}
    for n in names:
        rows = db.execute("select compiled, exact, score, diff_summary, strategy from attempts where func_addr=?", (addr[n],)).fetchall()
        b = None
        for c, e, sc, d, st in rows:
            g = (1, 10**9, 10**9) if not c else (0, 0, 0) if e else (0, *signals.distances(d or ""))
            if b is None or (g, -(sc or 0)) < (b[0], -b[1]):
                b = (g, sc or 0, st or "")
        out[n] = b
    return out


new_db, new_js, old_db, old_js = sys.argv[1:5]
new = {json.loads(l)["function"]: json.loads(l) for l in Path(new_js).read_text().splitlines()}
old = {json.loads(l)["function"]: json.loads(l) for l in Path(old_js).read_text().splitlines()}
names = sorted(set(new) & set(old))
nb, ob = best(new_db, names), best(old_db, names)
wins = [n for n in names if nb[n][0] < ob[n][0]]
losses = [n for n in names if nb[n][0] > ob[n][0]]
k, m = len(wins), len(wins) + len(losses)
p = sum(math.comb(m, i) for i in range(k, m + 1)) / 2 ** m if m else 1.0
print(f"paired {len(names)}: new wins {len(wins)} losses {len(losses)} ties {len(names) - m}; one-sided p = {p:.4f}")
print("wins:", wins); print("losses:", losses)
print("exact new:", [n for n in names if new[n].get("exact")], " old:", [n for n in names if old[n].get("exact")])
print(f"mean best-score difference: {sum(nb[n][1] - ob[n][1] for n in names) / len(names):+.3f}")
print("compiles new:", sum(new[n].get("compiles") or 0 for n in names), " old:", sum(old[n].get("compiles") or 0 for n in names))
print("wins via mined edit:", sum(":mined:" in nb[n][2] for n in wins), "; of those via a validated (promoted) template or localised pick: see strategies",
      [nb[n][2].split(":", 2)[-1][:60] for n in wins])

"""Small functions stalled on width in the focus arm: best source and the extension rows of its diff."""
import json, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import residual_classes as rc
db = sqlite3.connect("file:/home/grant/decomp/runs/composed-edits-20260929/focus.sqlite?mode=ro", uri=True)
addr = {n: a for a, n in db.execute("select addr, name from functions")}
rows = [json.loads(l) for l in (HERE / "focus-B207.jsonl").read_text().splitlines()]
shown = 0
for r in rows:
    if r["band"] != "small" or shown >= int(sys.argv[1] if len(sys.argv) > 1 else 3):
        continue
    atts = db.execute("select source_code, diff_summary from attempts where func_addr=? and compiled=1", (addr[r["function"]],)).fetchall()
    best = min(atts, key=lambda a: tuple(rc.counts(a[1] or "")[k] for k in rc.CLASSES))
    c = rc.counts(best[1] or "")
    if c["control_flow"] or not c["width"]:
        continue
    shown += 1
    print("=" * 30, r["function"], c)
    src = best[0]
    body = src[src.find(r["function"] + "("):]
    print(body[:1800])
    print("--- diff rows around extensions:")
    lines = (best[1] or "").splitlines()
    for i, l in enumerate(lines):
        if l[:1] in "+-" and not l.startswith(("+++", "---")) and rc.EXTENSION.match(l[1:].strip()):
            print("\n".join(lines[max(3, i - 4):i + 4])); print("   ...")

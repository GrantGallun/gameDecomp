"""Print one width-stalled function: declarations, the lines around extra extensions, and the edits the search tried."""
import json, re, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import residual_classes as rc
db = sqlite3.connect("file:/home/grant/decomp/runs/composed-edits-20260929/focus2.sqlite?mode=ro", uri=True)
addr = {n: a for a, n in db.execute("select addr, name from functions")}
stalls = json.loads((HERE / "width_stalls.json").read_text())
names = sys.argv[1:] or [n for n, v in stalls.items() if v["band"] in ("small", "medium")][:3]
for n in names:
    atts = db.execute("select strategy, source_code, diff_summary, compiled from attempts where func_addr=? order by id", (addr[n],)).fetchall()
    src, diff = min(((s, d) for _st, s, d, c in atts if c), key=lambda a: tuple(rc.counts(a[1] or "")[k] for k in rc.CLASSES))
    print("=" * 20, n, stalls[n]["delta"], "compiles", stalls[n]["compiles"])
    body = src[src.find(n + "("):]
    print(body[:1400])
    print("-- tried:", sorted({st.split(":")[1] for st, *_ in atts if ":" in st})[:12])
    print("-- tried labels (decl/type):", [st.split(":", 2)[-1][:60] for st, *_ in atts if st.split(":")[1:2] in (["decl"], ["type"])][:10])
    lines = (diff or "").splitlines()
    for i, l in enumerate(lines):
        if l.startswith("+") and not l.startswith("+++") and rc.EXTENSION.match(l[1:].strip()):
            print("   " + " | ".join(x.strip() for x in lines[max(3, i - 3):i + 3]))

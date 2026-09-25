"""FIT-only inspection: call sites of a few FIT functions labeled with one struct (development diagnostics)."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score
rows = json.loads((score.E / "facts.json").read_text())
lab, _ = score.labels(rows)
name_of = {r["addr"]: r["function"] for r in rows}
want = sys.argv[1] if len(sys.argv) > 1 else "EndingCreditsSlash"
fns = [k[1] for k, v in lab.items() if v == want and score.half(k[1]) == "FIT"][:4]
byname = {r["function"]: r for r in rows}
for f in fns:
    r = byname[f]
    print("==", f, "accesses on P0:", sum(1 for a in r["accesses"] if a[0] == ["P", f, 0]))
    for site in score.call_sites(r):
        print("   ", name_of.get(site[0][0], hex(site[0][0])),
              [(k, x if x[0] != "G" else ("G", name_of.get(x[1], hex(x[1])))) for _t, k, x in site])

"""Paired comparison of the composed arm (trial.sqlite) and the class-key arm (class.sqlite).

Per function and arm: exact, and the lowest (control_flow, width) and lowest full class key reached by
any compiled attempt. Run in WSL after both arms finish.
"""
import collections, json, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import residual_classes

RUNS = Path("/home/grant/decomp/runs/composed-edits-20260929")
ARMS = {"composed": ("trial.sqlite", "results"), "class": ("class.sqlite", "class")}
if len(sys.argv) > 1 and sys.argv[1] == "focus2":
    ARMS = {"composed": ("focus.sqlite", "focus"), "class": ("focus2.sqlite", "focus2")}   # focus arm vs corrected focus arm
elif len(sys.argv) > 1 and sys.argv[1] == "focus":
    ARMS = {"composed": ("class.sqlite", "class"), "class": ("focus.sqlite", "focus")}   # class arm vs focus arm


def per_function(db_name, names):
    db = sqlite3.connect(f"file:{RUNS / db_name}?mode=ro", uri=True)
    addr = {n: a for a, n in db.execute("select addr, name from functions")}
    out = {}
    for n in names:
        rows = db.execute("select compiled, exact, diff_summary from attempts where func_addr=?", (addr[n],)).fetchall()
        exact = any(e for _c, e, _d in rows)
        best = None
        for c, e, d in rows:
            if not c:
                continue
            k = residual_classes.counts(d or "")
            v = tuple(k[x] for x in residual_classes.CLASSES)
            best = v if best is None or v < best else best
        base = next(((residual_classes.counts(d or "")) for c, e, d in rows if c), None)
        out[n] = {"exact": exact, "best": best}
    return out


for frame in ("A", "B207"):
    files = {arm: HERE / f"{prefix}-{frame}.jsonl" for arm, (_db, prefix) in ARMS.items()}
    if frame == "B207" and ARMS["composed"][1] == "results":
        files["composed"] = HERE / "results-B.jsonl"
    if not all(f.exists() for f in files.values()):
        print(frame, "missing results"); continue
    names = {arm: {json.loads(l)["function"] for l in f.read_text().splitlines() if l.strip()} for arm, f in files.items()}
    common = sorted(set.intersection(*names.values()))
    res = {arm: per_function(ARMS[arm][0], common) for arm in ARMS}
    c = collections.Counter()
    for n in common:
        a, b = res["composed"][n], res["class"][n]
        c["composed exact"] += a["exact"]; c["class exact"] += b["exact"]
        c["exact only in class"] += b["exact"] and not a["exact"]
        c["exact only in composed"] += a["exact"] and not b["exact"]
        if a["best"] and b["best"]:
            c["class lower on control_flow+width"] += (b["best"][0], b["best"][1]) < (a["best"][0], a["best"][1])
            c["composed lower on control_flow+width"] += (a["best"][0], a["best"][1]) < (b["best"][0], b["best"][1])
            c["class better full key"] += b["best"] < a["best"]
            c["composed better full key"] += a["best"] < b["best"]
            c["class reached control_flow=0,width=0"] += b["best"][0] == 0 and b["best"][1] == 0
            c["composed reached control_flow=0,width=0"] += a["best"][0] == 0 and a["best"][1] == 0
    print(frame, len(common), json.dumps(dict(c), indent=1))
    print("  exact only in class:", [n for n in common if res["class"][n]["exact"] and not res["composed"][n]["exact"]])
    print("  exact only in composed:", [n for n in common if res["composed"][n]["exact"] and not res["class"][n]["exact"]])

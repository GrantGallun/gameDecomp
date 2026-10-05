import json, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
r = {x["function"]: x for x in json.load(open(HERE / "near_miss.json"))}
for fn in sys.argv[1:]:
    x = r[fn]
    db = sqlite3.connect(f"file:{x['ledger']}?mode=ro", uri=True)
    src, diff = db.execute("select source_code, diff_summary from attempts where id=?", (x["attempt_id"],)).fetchone()
    i = src.find(fn + "(")
    print("=" * 30, fn, x["gradient"])
    print(src[max(0, src.rfind("\n", 0, i)):][:2600])
    lines = diff.splitlines()
    idx = [k for k, l in enumerate(lines) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    shown = set()
    for k in idx:
        for j in range(max(0, k - 3), min(len(lines), k + 4)):
            if j not in shown:
                shown.add(j)
                print(lines[j])
        print("..")

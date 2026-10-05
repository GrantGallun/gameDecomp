import json, sqlite3, sys, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
r = json.load(open(HERE / "near_miss.json"))
ro = [x for x in r if set(x["classes"]) == {"registers"}]
for x in ro:
    db = sqlite3.connect(f"file:{x['ledger']}?mode=ro", uri=True)
    strat, created = db.execute("select strategy, created_at from attempts where id=?", (x["attempt_id"],)).fetchone() if True else (None, None)
    counts = collections.Counter()
    for path in ("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite", "/home/grant/decomp/kb-sbk1.sqlite"):
        d2 = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for (s,) in d2.execute("select a.strategy from attempts a join functions f on f.addr=a.func_addr where f.name=?", (x["function"],)):
            s = s or ""
            fam = "regalloc" if ("regalloc" in s or "nearmiss" in s or "register" in s) else s.split(":")[0][:20]
            counts[fam] += 1
    print(x["function"], x["gradient"], "best:", (strat or "")[:50], "| regalloc attempts:", counts.get("regalloc", 0), "| total:", sum(counts.values()))

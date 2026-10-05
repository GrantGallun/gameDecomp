import json, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import nearmiss_llm
REPO = Path("/home/grant/decomp/sbk1")
r = {x["function"]: x for x in json.load(open("/mnt/c/Code/gameDecomp/eval/results/loop-shape-20260930/near_miss.json"))}
fn = sys.argv[1]
x = r[fn]
db = sqlite3.connect(x["ledger"])
src, diff = db.execute("select source_code, diff_summary from attempts where id=?", (x["attempt_id"],)).fetchone()
t = time.time()
kids = nearmiss_llm.children(src, fn, diff, REPO)
print(f"{len(kids)} children in {time.time() - t:.0f}s")
for label, kind, child in kids:
    print(" ", label)
if len(sys.argv) > 2:
    from solver import llm
    text, _ = llm.generate(nearmiss_llm.ENDPOINT, nearmiss_llm.MODEL, nearmiss_llm.prompt(src, fn, diff), timeout=240,
                           num_predict=3000, think="low", temperature=0.4, seed=0, response_schema=nearmiss_llm.SCHEMA)
    print(text[:3000])

"""DEV-only iteration loop for the BINARY generator (PROTOCOL A5): run the first 150, keep rows, bucket errors."""
import collections, concurrent.futures, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablate, run_binary as rb
OUT = ablate.E / "rows_binary_dev"
OUT.mkdir(parents=True, exist_ok=True)
rb.OUT = OUT
rows = ablate.sample(600)[:150]
m = rb.pairs.mirror_repo()
res = []
with concurrent.futures.ThreadPoolExecutor(4) as pool:
    for x in pool.map(lambda r: rb.one(m, r), rows):
        (OUT / f"{x['function']}.json").write_text(json.dumps(x))
        res.append(x)
c = collections.Counter(x["status"] for x in res)
errs = collections.Counter()
for x in res:
    if x["status"] == "not-compiled":
        first = re.search(r"cfe: Error: [^\n]*?, line \d+: ([^\n]+)", x["error"]) or re.search(r"Error: ([^\n]+)", x["error"])
        key = re.sub(r"'[^']*'", "'X'", first.group(1)) if first else x["error"][-80:]
        errs[key[:90]] += 1
print(dict(c))
for k, v in errs.most_common(14):
    print(v, k)

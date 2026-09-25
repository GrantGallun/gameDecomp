"""Bucket the frontend-gate diagnostics of binary-types functions whose object matched but whose C the gate refused."""
import collections
import json
import re
import sqlite3
from pathlib import Path

KB = Path.home() / "decomp/kb-sbk1.sqlite"
conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
rows = conn.execute("select f.name, a.sampling, a.exact from attempts a join functions f on f.addr=a.func_addr "
                    "where a.run_id like 'binary-types%' and a.run_id != 'binary-types-gatefix-20260924' order by a.id").fetchall()
passed, last = set(), {}
for name, sampling, exact in rows:
    fe = (json.loads(sampling) or {}).get("frontend") if sampling else None
    if exact and fe and fe.get("passed"):
        passed.add(name)
    last[name] = fe
buckets, examples, failed = collections.Counter(), {}, []
for name, fe in last.items():
    if name in passed or not fe or fe.get("passed") is not False:
        continue
    failed.append(name)
    errs = re.findall(r"error: ([^\[\n]+)", fe.get("diagnostics", ""))
    for key in {re.sub(r"'[^']*'", "X", e).strip()[:80] for e in errs}:
        buckets[key] += 1
        examples.setdefault(key, name)
print("gate-failed, never confirmed:", len(failed))
for k, v in buckets.most_common(15):
    print(v, "|", k, "|", examples[k])
Path(__file__).with_suffix(".json").write_text(json.dumps({"failed": sorted(failed), "buckets": buckets.most_common()}, indent=1))

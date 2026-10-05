"""Why do re-verifications of previously compiling sources fail? Read-only, campaign DB.

For every reverify row that failed, find an earlier attempt with the SAME source text that compiled.
The source is identical, so any difference is the compile CONTEXT: headers, recipe, workspace.
Reports: error signatures, the time gap between the good compile and the failure, how the failures
cluster in time and across functions, and whether the good compile and the failure used the same
compiler recipe.
"""
import collections
import datetime
import hashlib
import json
import re
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
first_ok = {}
for aid, sha, src, created, sampling in db.execute(
        "select id, source_sha256, source_code, created_at, sampling from attempts "
        "where coalesce(compiled,0)=1 and source_code is not null order by id"):
    key = sha or hashlib.sha256(src.encode()).hexdigest()
    first_ok.setdefault(key, (aid, created, sampling))


def ts(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        try:
            return datetime.datetime.fromisoformat(str(x)).timestamp()
        except ValueError:
            return None


def recipe_of(sampling):
    try:
        data = json.loads(sampling or "{}")
    except ValueError:
        return None
    ver = data.get("verification") or {}
    rec = ver.get("compiler_recipe") or data.get("compiler_recipe") or {}
    return rec.get("command_sha256") or rec.get("sha256") or json.dumps(rec, sort_keys=True)[:80] or None


sig = collections.Counter()
gap = collections.Counter()
per_fn = collections.Counter()
per_day = collections.Counter()
same_recipe = collections.Counter()
never_ok = 0
total = 0
examples = []
for aid, name, sha, src, created, err, sampling, strategy in db.execute(
        "select a.id, f.name, a.source_sha256, a.source_code, a.created_at, a.compiler_stderr, "
        "a.sampling, a.strategy from attempts a join functions f on f.addr=a.func_addr "
        "where a.strategy like '%reverify%' and coalesce(a.compiled,0)=0 and a.source_code is not null"):
    total += 1
    key = sha or hashlib.sha256(src.encode()).hexdigest()
    ok = first_ok.get(key)
    if ok is None:
        never_ok += 1
        continue
    first = next((l for l in (err or "").splitlines() if "rror" in l), "<no error line>")
    s = re.sub(r"'[^']*'", "'X'", re.sub(r"line \d+|: \d+:", "line N", first))[:90]
    sig[s] += 1
    per_fn[name] += 1
    t0, t1 = ts(ok[1]), ts(created)
    if t0 and t1:
        g = t1 - t0
        gap["<0 (failure BEFORE the good compile)" if g < 0 else "<1h" if g < 3600 else
            "<1d" if g < 86400 else "<7d" if g < 604800 else ">=7d"] += 1
        per_day[datetime.datetime.fromtimestamp(t1).strftime("%m-%d")] += 1
    ra, rb = recipe_of(ok[2]), recipe_of(sampling)
    same_recipe["unknown" if not (ra and rb) else "same" if ra == rb else "DIFFERENT"] += 1
    if len(examples) < 6 and s != "<no error line>":
        examples.append({"function": name, "reverify": aid, "good": ok[0], "error": first[:160]})
print("failed reverify rows:", total, "| source never compiled anywhere:", never_ok,
      "| had an identical compiling source:", total - never_ok)
print("\nfirst-error signatures:")
for k, v in sig.most_common(10):
    print(f"  {v:6d}  {k}")
print("\ngap from the good compile to the failure:", dict(gap))
print("failures by day:", dict(sorted(per_day.items())))
print("compiler recipe, good vs failed:", dict(same_recipe))
print("functions affected:", len(per_fn), "| top:", per_fn.most_common(6))
print("examples:", json.dumps(examples, indent=1))

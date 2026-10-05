"""Probe the declared-identifier check before trusting it. Read-only.

On sources that COMPILED, every identifier is declared -- so anything `undeclared` flags there is a
false positive. On sources whose first IDO error was "'X' undefined", the check should flag X.
"""
import collections
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import compile_context as cc  # noqa: E402

REPO = Path("/home/grant/decomp/sbk1")
db = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro",
                     uri=True)


def sample(sql, n=400):
    rows = db.execute(sql).fetchall()
    rows.sort(key=lambda r: hashlib.sha256(str(r[0]).encode()).hexdigest())
    return rows[:n]


known_cache = {}


def check(name, source):
    try:
        sp = cc.split(source, name)
    except ValueError:
        return None
    key = hashlib.sha256(sp.context.encode()).hexdigest()
    if key not in known_cache:
        known_cache[key] = cc.symbols(REPO, sp.context)
    return cc.undeclared(sp.body, known_cache[key])


fp = collections.Counter()
examples = []
checked = flagged = 0
for aid, name, source in sample(
        "select a.id, f.name, a.source_code from attempts a join functions f on f.addr=a.func_addr "
        "where a.compiled=1 and a.source_code is not null group by a.func_addr"):
    res = check(name, source)
    if res is None:
        continue
    checked += 1
    if res:
        flagged += 1
        for r in res:
            fp[r["name"]] += 1
        if len(examples) < 8:
            examples.append({"function": name, "flagged": [r["name"] for r in res][:5]})
print(json.dumps({"compiled_sources_checked": checked, "false_positive_sources": flagged,
                  "false_positive_rate": round(flagged / max(checked, 1), 3),
                  "most_common_false_names": fp.most_common(12), "examples": examples}, indent=1))

hit = total = 0
misses = []
for aid, name, source, err in sample(
        "select a.id, f.name, a.source_code, a.compiler_stderr from attempts a join functions f "
        "on f.addr=a.func_addr where a.compiled=0 and a.compiler_stderr like '%undefined%' "
        "and a.source_code is not null"):
    m = re.search(r"'(\w+)' undefined", err or "")
    res = check(name, source)
    if not m or res is None:
        continue
    total += 1
    if m.group(1) in {r["name"] for r in res}:
        hit += 1
    elif len(misses) < 6:
        misses.append({"function": name, "ido_undefined": m.group(1)})
print(json.dumps({"undefined_failures_checked": total, "check_flagged_the_same_name": hit,
                  "recall": round(hit / max(total, 1), 3), "misses": misses}, indent=1))

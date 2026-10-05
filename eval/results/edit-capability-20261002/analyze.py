"""Edit size per level: does more information make the model rewrite more of the function?

    python3 analyze.py   -> stdout + edit_size.json here
"""
import collections
import difflib
import json
import statistics
from pathlib import Path

import run

cases = {c["id"]: c for c in map(json.loads, open(run.E / "cases.jsonl"))}
rows = [json.loads(l) for l in open(run.E / "attempts.jsonl")]


def norm(d):
    return [l.strip() for l in d.strip().split("\n") if l.strip()]


by = collections.defaultdict(list)
for r in rows:
    if "def" not in r:
        continue
    c = cases[r["id"]]
    cur, new = norm(c["perturbed_def"]), norm(r["def"])
    sm = difflib.SequenceMatcher(a=cur, b=new, autojunk=False)
    changed = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")
    # Did the edit touch the planted site? (the perturbed line itself, or its line was removed/replaced)
    site = c["site_text"]
    touched = site not in new if site else None
    by[r["level"]].append({"changed": changed, "len": len(cur), "status": r["status"], "touched": touched,
                           "class": r["class"]})

out = {}
print(f"{'lvl':4} {'n':>3} {'median lines changed':>21} {'mean':>6} {'>=5 lines':>10} {'touched site':>13}"
      f" {'exact: median chg':>18} {'fail-compile: median chg':>25}")
for lv in run.LEVELS:
    xs = by.get(lv, [])
    if not xs:
        continue
    ch = [x["changed"] for x in xs]
    ex = [x["changed"] for x in xs if x["status"] == "exact"]
    nc = [x["changed"] for x in xs if x["status"] == "not-compiled"]
    t = [x["touched"] for x in xs if x["touched"] is not None]
    out[lv] = {"n": len(xs), "median_changed": statistics.median(ch), "mean_changed": statistics.mean(ch),
               "big_rewrites": sum(c >= 5 for c in ch), "touched_site": sum(t), "site_known": len(t),
               "exact_median_changed": statistics.median(ex) if ex else None,
               "notcompiled_median_changed": statistics.median(nc) if nc else None}
    o = out[lv]
    print(f"{lv:4} {o['n']:>3} {o['median_changed']:>21} {o['mean_changed']:>6.1f} {o['big_rewrites']:>10}"
          f" {o['touched_site']:>6}/{o['site_known']:<6} {str(o['exact_median_changed']):>18}"
          f" {str(o['notcompiled_median_changed']):>25}")
(Path(__file__).resolve().parent / "edit_size.json").write_text(json.dumps(out, indent=1))

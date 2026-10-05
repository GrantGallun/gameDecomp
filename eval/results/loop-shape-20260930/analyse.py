"""L1 analysis against PREREGISTRATION.md: P1 firing, P2 skeleton, P3 gradient, P4 exact."""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
rows = [json.loads(l) for l in (HERE / (sys.argv[1] if len(sys.argv) > 1 else "l1.jsonl")).read_text().splitlines()]
census = {json.loads(l)["function"]: json.loads(l) for l in (ROOT / "eval/results/draft-census-20260930/rows.jsonl").read_text().splitlines()}


def best(recs, key):
    vals = [tuple(r[key]) if isinstance(r[key], list) else r[key] for r in recs if r.get("compiled") and key in r]
    return min(vals) if vals else None


def sign(k, m):
    return sum(math.comb(m, i) for i in range(k, m + 1)) / 2 ** m if m else 1.0


goto = [r for r in rows if census.get(r["function"], {}).get("draft", {}).get("goto")]
fired = [r for r in goto if any(v for v in r.get("variants_per_draft", []))]
print(f"functions {len(rows)}, errors {sum('error' in r for r in rows)}, no drafts {sum(not r.get('drafts') for r in rows)}")
print(f"P1 firing on goto drafts: {len(fired)}/{len(goto)} = {len(fired) / max(1, len(goto)):.0%} (predicted >= 70%)")
both = [r for r in rows if best(r["raw"], "gradient") and best(r["raw"], "gradient")[0] == 0]
sk_better = sk_worse = gr_better = gr_worse = 0
details = []
for r in both:
    union = r["raw"] + r["loops"]
    rs, us = best(r["raw"], "skeleton"), best(union, "skeleton")
    rg, ug = best(r["raw"], "gradient"), best(union, "gradient")
    sk_better += us is not None and rs is not None and us < rs
    gr_better += ug < rg
    details.append((r["function"], rs, us, rg, ug))
print(f"compiling functions {len(both)}")
print(f"P2 skeleton strictly better: {sk_better}/{len(both)} = {sk_better / max(1, len(both)):.0%} (predicted >= 30%)")
print(f"P3 gradient strictly better: {gr_better}/{len(both)} = {gr_better / max(1, len(both)):.0%} (predicted >= 25%)")
raw_exact = {r["function"] for r in rows if any(x["exact"] for x in r["raw"])}
loop_exact = {r["function"] for r in rows if any(x["exact"] for x in r["loops"])}
print(f"P4 exact: loops-only {sorted(loop_exact - raw_exact)}, raw {sorted(raw_exact)}")
# how the loop variants alone compare to the raw drafts (not the union): does shaping help or hurt on average?
w = l = 0
for r in both:
    lg = best(r["loops"], "gradient")
    if lg is None:
        continue
    rg = best(r["raw"], "gradient")
    w += lg < rg
    l += lg > rg
print(f"variants-only vs raw best gradient: {w} better / {l} worse, one-sided p = {sign(w, w + l):.4f}")
sk_zero_raw = sum(1 for d in details if d[1] == 0)
sk_zero_union = sum(1 for d in details if d[2] == 0)
print(f"skeleton distance 0: raw {sk_zero_raw}, union {sk_zero_union} of {len(details)}")
labels = {}
for r in rows:
    rg = best(r["raw"], "gradient")
    for x in r["loops"]:
        if x.get("compiled") and rg and tuple(x["gradient"]) < rg:
            k = x["label"].split("|")[1].split("(")[0]
            labels[k] = labels.get(k, 0) + 1
print("improving variant kinds:", labels)

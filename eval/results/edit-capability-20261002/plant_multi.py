"""Multi-edit planted panels for coverage at a budget: k planted edits per case, split by FUNCTION.

    python3 plant_multi.py [--jobs 3]   -> E/multi_{train,dev,heldout}.jsonl, plant_multi_tally.json here

The single-edit panels are saturated (dev 51/51, held-out 41/42), so they can no longer show a coverage gain; with
k = 2 or 3 edits the search needs depth and its budget binds. Functions used by cases.jsonl or heldout.jsonl are
excluded, and each remaining function goes to exactly one split (train 1/2, dev 1/4, held-out 1/4, by hash).

Every step must compile and change the masked object relative to the step before it: a step the compiler erases is
not planted, so a case's k is the number of VISIBLE edits. Steps go on distinct lines. train is for fitting search
priors; dev for development; held-out is frozen before any arm is scored (HELDOUT_MULTI.md).
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json

import run
from run import E, mine

SALT = "multi-v1"
KS = (2, 3)
SEQUENCES_PER_K = 16       # class sequences tried per (function, k) before giving up
CASES_PER_K = 2            # distinct class sets per (function, k); the split is by function, so they stay together
# v1 drew sequences from all 11 classes and planted 29 cases on 95 functions: most sequences named a class with no
# site in the function (a cast, a plain if, a non-void return). Sequences now draw from the classes that apply.


def split_of(name: str) -> str:
    h = int(hashlib.sha256(f"{SALT}:{name}".encode()).hexdigest(), 16) % 4
    return "train" if h < 2 else ("dev" if h == 2 else "heldout")


def _h(*parts) -> str:
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


def sequences(name: str, k: int, classes):
    seen = set()
    for i in range(200):
        seq = tuple(sorted(classes, key=lambda c: _h(SALT, name, k, i, c))[:k])
        if len(seq) == k and frozenset(seq) not in seen:
            seen.add(frozenset(seq))
            yield seq


def plant_one(r: dict, k: int, tally: collections.Counter, avoid=()):
    name = r["function"]
    parts = run.split(r["source"], name)
    if not parts:
        tally["split-failed"] += 1
        return None
    head, d, tail = parts
    b = run.build(name, "mp_orig", r["source"])
    if not b["compiled"] or not b.get("certified"):     # admission: the original must certify against target.o
        tally["original-not-certified"] += 1
        return None
    target = mine.mask(b["dump"])
    lines0 = run._lines(d)
    try:
        applicable = [cls for cls, fn in sorted(run.PERTURB.items()) if fn(lines0, run._body_range(lines0))]
    except (ValueError, StopIteration):
        tally["no-body"] += 1
        return None
    for seq in [s for s in sequences(name, k, applicable) if frozenset(s) not in avoid][:SEQUENCES_PER_K]:
        lines, sites, steps, previous, last = run._lines(d), [], [], target, None
        for cls in seq:
            try:
                rng = run._body_range(lines)
            except (ValueError, StopIteration):
                break
            cands = [(nl, s) for nl, s in run.PERTURB[cls](lines, rng) if all(abs(s - t) > 1 for t in sites)]
            cands.sort(key=lambda c: _h(cls, name, c[1], len(steps)))
            for new_lines, site in cands[:4]:
                bb = run.build(name, f"mp_{cls}", head + "\n".join(new_lines) + tail)
                if not bb["compiled"]:
                    tally["step-not-compiling"] += 1
                    continue
                dump = mine.mask(bb["dump"])
                if dump == previous:
                    tally["step-invisible"] += 1
                    continue
                steps.append({"class": cls, "site": site, "text": new_lines[site].strip() if site < len(new_lines) else ""})
                lines, previous, last = new_lines, dump, bb
                sites.append(site)
                break
            else:
                break
        if len(steps) == k and previous != target:
            tally[f"planted-k{k}"] += 1
            return {"id": f"k{k}.{len(avoid)}:{name}", "class": f"k{k}", "classes": [s["class"] for s in steps], "kind": "multi",
                    "function": name, "head": head, "tail": tail, "original_def": d,
                    "perturbed_def": "\n".join(lines), "steps": steps, "target": target, "current": previous,
                    "score": last["score"], "diff": mine.gnu_diff(target, previous)}
        tally[f"sequence-failed-k{k}"] += 1
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()
    used = set()
    for f in ("cases.jsonl", "heldout.jsonl"):
        used |= {json.loads(line)["function"] for line in open(E / f)}
    rows = [r for r in run.pool() if (r.get("t_len") or 0) <= run.MAX_TARGET_LINES and r["function"] not in used]
    print(f"{len(rows)} functions outside dev and held-out", flush=True)
    tallies = collections.defaultdict(collections.Counter)

    def work(r):
        t = collections.Counter()
        out = []
        for k in KS:
            avoid = set()
            for _ in range(CASES_PER_K):
                c = plant_one(r, k, t, frozenset(avoid))
                if not c:
                    break
                out.append(c)
                avoid.add(frozenset(c["classes"]))
        return r["function"], out, t

    by_split = collections.defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        for name, cases, t in ex.map(work, rows):
            s = split_of(name)
            tallies[s].update(t)
            tallies[s]["functions"] += 1
            by_split[s].extend(cases)
            print(s, "k=" + ",".join(c["class"][1:] for c in cases) if cases else "-", flush=True)
    for s in ("train", "dev", "heldout"):
        with open(E / f"multi_{s}.jsonl", "w") as f:
            for c in sorted(by_split[s], key=lambda c: c["id"]):
                f.write(json.dumps(c) + "\n")
        print(s, len(by_split[s]), dict(tallies[s]))
    (run.HERE / "plant_multi_tally.json").write_text(json.dumps({s: dict(t) for s, t in tallies.items()}, indent=1))


if __name__ == "__main__":
    main()

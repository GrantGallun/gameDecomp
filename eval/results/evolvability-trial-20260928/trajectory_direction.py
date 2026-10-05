"""Direction over the whole searched trajectory, not only the returned best source. EVALUATION ONLY.

Astra's review (docs/evolvability-diagnosis-review-20260928.md, finding 1): diagnose.py graded `best_source`,
which a plateau leaves at the root even after the search explored other shapes, so "same distance as the
root" did not show the search never moved toward the answer. Here every recorded source an arm compiled is
graded against the reference with the same structural key distance; per function and arm: the closest any
explored source got, and whether that is closer than the root. The reference grades only; nothing is fed
back (memory key-distance-dev-only). Structural distance is an incomplete proxy (casts removed, field
spellings normalized); certificates remain the only authority on matches.

    python3 trajectory_direction.py
"""
import collections
import json
import sys
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/refinement-data-20260927/analysis")
from key_distance import REFERENCE, canonical  # noqa: E402
from patterns.commit_provenance import function_definitions  # noqa: E402

refs = {}
for path in REFERENCE.rglob("*.c"):
    try:
        for name, text in function_definitions(path.read_text(errors="replace")).items():
            refs.setdefault(name, text)
    except Exception:
        continue
keys = {}


def dist(src, name):
    if name not in refs:
        return None
    if name not in keys:
        keys[name] = canonical(refs[name], None, True)
    cand = canonical(src, name, True)
    return None if keys[name] is None or cand is None else Levenshtein.normalized_distance(cand, keys[name])


table = collections.defaultdict(collections.Counter)
gains = collections.defaultdict(list)
for exp in ("evolvability-trial-20260928", "evolvability-replication-20260928"):
    for p in sorted(Path(f"/home/grant/decomp/experiments/{exp}").glob("run-*/**/result.json")):
        r = json.loads(p.read_text())
        if r.get("seed", 0) != 0:
            continue
        name, arm = r["function"], r["arm"]
        sources = [e["source"] for e in r.get("events") or [] if e.get("source")]
        if not sources:
            continue
        root = dist(sources[0], name)
        if root is None:
            table[arm]["no reference"] += 1
            continue
        explored = [d for d in (dist(s, name) for s in sources[1:]) if d is not None]
        closest = min(explored) if explored else root
        best = dist(r.get("best_source") or sources[0], name)
        t = table[arm]
        t["functions"] += 1
        t["explored closer than root"] += closest < root - 1e-9
        t["returned best closer than root"] += best is not None and best < root - 1e-9
        t["exact"] += bool(r.get("exact"))
        gains[arm].append(root - closest)
for arm, t in sorted(table.items()):
    g = sorted(gains[arm])
    print(f"{arm:24s} " + ", ".join(f"{k} {v}" for k, v in sorted(t.items()))
          + f" | median closest-gain {g[len(g) // 2]:.3f}, max {g[-1]:.3f}")

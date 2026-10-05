"""Score PREREGISTRATION-vocabulary.md (V1-V3). V2 uses the reference as an evaluation-only grader.

    python3 score_vocabulary.py
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

E = Path("/home/grant/decomp/experiments/evolvability-vocabulary-20260928")
A, B = "production_coalesce", "evolvability_coalesce"
rows = [json.loads(p.read_text()) for p in sorted(E.glob("run-*/**/result.json"))]
by = {(r["arm"], r["function"]): r for r in rows}
functions = sorted({f for (_a, f) in by if (A, f) in by and (B, f) in by})
refs = {}
for path in REFERENCE.rglob("*.c"):
    try:
        for name, text in function_definitions(path.read_text(errors="replace")).items():
            refs.setdefault(name, text)
    except Exception:
        continue


def dist(src, name):
    if name not in refs:
        return None
    key, cand = canonical(refs[name], None, True), canonical(src, name, True)
    return None if key is None or cand is None else Levenshtein.normalized_distance(cand, key)


def gradient(r):
    return (0, 0, 0) if r.get("exact") else tuple(r.get("best_compiled_gradient") or (float("inf"),))


exact = {a: {f for f in functions if by[(a, f)].get("exact")} for a in (A, B)}
print(f"functions complete in both arms: {len(functions)}")
print(f"V1 matched: {A} {sorted(exact[A])} | {B} {sorted(exact[B])} ->",
      len(exact[B]) >= len(exact[A]) and not (exact[A] - exact[B]))
kept, explored = collections.Counter(), collections.Counter()
for f in functions:
    for a in (A, B):
        r = by[(a, f)]
        sources = [e["source"] for e in r.get("events") or [] if e.get("source")]
        root = dist(sources[0], f) if sources else None
        if root is None:
            continue
        best = dist(r.get("best_source") or sources[0], f)
        kept[a] += best is not None and best < root - 1e-9
        explored[a] += any((d := dist(s, f)) is not None and d < root - 1e-9 for s in sources[1:])
print(f"V2 returned best closer than root: {A} {kept[A]}, {B} {kept[B]} ->", kept[B] > kept[A],
      f"| explored some closer source: {A} {explored[A]}, {B} {explored[B]}")
cmp = collections.Counter()
for f in functions:
    gb, ga = gradient(by[(B, f)]), gradient(by[(A, f)])
    cmp["better" if gb < ga else "worse" if gb > ga else "tie"] += 1
print(f"V3 {B} vs {A} gradient: {dict(cmp)} ->", cmp["better"] >= cmp["worse"])
for a in (A, B):
    spent = sorted(by[(a, f)].get("budget_spent") or 0 for f in functions)
    print(f"  {a} budget spent median {spent[len(spent) // 2]:.1f}" if spent else "")

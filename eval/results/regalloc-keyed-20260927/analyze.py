"""Score the preregistered predictions (PREREGISTRATION.md) from results.jsonl. Read-only.

    python3 eval/results/regalloc-keyed-20260927/analyze.py [results.jsonl]
"""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else
            "/home/grant/decomp/experiments/regalloc-keyed-20260927/results.jsonl")
rows = [json.loads(l) for l in path.read_text().splitlines()]
done = [r for r in rows if r.get("status") == "done"
        and all(r["arms"].get(a, {}).get("status") == "done" for a in ("plain", "keyed", "keyed+ranked"))]
print(f"functions: {len(rows)} run, {len(done)} with all three arms done")
for r in rows:
    if r not in done:
        print("  incomplete:", r["function"], r.get("error") or {a: v.get("status") for a, v in r.get("arms", {}).items()})


def arm(a):
    return [r["arms"][a] for r in done]


# K1: share of evaluations resolved by key
keyed = arm("keyed")
evaluated = sum(v["evaluated"] for v in keyed)
resolved = sum(v["keyed"] for v in keyed)
print(f"K1 key-resolved evaluations: {resolved}/{evaluated} = {resolved / max(evaluated, 1):.1%}  (predicted >= 25%)")
# K2: violations
violations = sum(v["key_violations"] for a in ("keyed", "keyed+ranked") for v in arm(a))
print(f"K2 key violations: {violations}  (predicted 0)")
# K3: path prefix
diverged = []
for r in done:
    p, k = r["arms"]["plain"]["path"], r["arms"]["keyed"]["path"]
    n = min(len(p), len(k))
    if p[:n] != k[:n]:
        first = next(i for i in range(n) if p[i] != k[i])
        diverged.append((r["function"], first, p[first], k[first]))
print(f"K3 functions whose keyed path diverged from plain: {len(diverged)}  (predicted 0)")
for d in diverged[:10]:
    print("   ", d)
longer = sum(1 for r in done if len(r["arms"]["keyed"]["path"]) > len(r["arms"]["plain"]["path"]))
print(f"   keyed evaluated more candidates than plain in {longer}/{len(done)} functions")
# K4: matches
exact = {a: {r["function"] for r in done if r["arms"][a]["exact"]} for a in ("plain", "keyed", "keyed+ranked")}
print(f"K4 matches: plain {len(exact['plain'])}, keyed {len(exact['keyed'])}; "
      f"plain-only {sorted(exact['plain'] - exact['keyed'])}, keyed-only {sorted(exact['keyed'] - exact['plain'])}")
# K5: measured key/compile time ratio
ks = sum(v["key_seconds"] for a in ("keyed", "keyed+ranked") for v in arm(a))
kn = sum(v["keys"] for a in ("keyed", "keyed+ranked") for v in arm(a))
cs = sum(v["compile_seconds"] for a in ("plain", "keyed", "keyed+ranked") for v in arm(a))
cn = sum(v["compiles"] for a in ("plain", "keyed", "keyed+ranked") for v in arm(a))
ratio = (ks / max(kn, 1)) / (cs / max(cn, 1))
print(f"K5 key {ks / max(kn, 1):.3f}s vs compile {cs / max(cn, 1):.3f}s -> ratio {ratio:.3f}  (predicted <= 0.2)")
# R1
print(f"R1 matches: keyed+ranked {len(exact['keyed+ranked'])} vs keyed {len(exact['keyed'])}; "
      f"ranked-only {sorted(exact['keyed+ranked'] - exact['keyed'])}, lost {sorted(exact['keyed'] - exact['keyed+ranked'])}")
# cost and progress
for a in ("plain", "keyed", "keyed+ranked"):
    v = arm(a)
    improved = sum(1 for x in v if x["exact"] or (x["best_gradient"] and x["baseline_gradient"]
                                                 and x["best_gradient"] < x["baseline_gradient"]))
    print(f"   {a:13s} spent {sum(x['spent'] for x in v):8.1f}  evaluated {sum(x['evaluated'] for x in v):6d}"
          f"  wall {sum(x['seconds'] for x in v):7.0f}s  improved {improved}  exact {sum(x['exact'] for x in v)}")

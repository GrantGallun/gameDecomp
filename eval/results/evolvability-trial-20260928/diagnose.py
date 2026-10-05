"""What is going wrong in the mutation-selection trials, and did any arm move the right way?
TOOL DEVELOPMENT ONLY: the reference decomp grades candidates here (memory key-distance-dev-only); nothing
is fed back into a search, prompt, KB or dataset.

Per function (both trials, 78) and arm:
  residual   the root's gradient (non_register, reg_insns, reg_operands): non_register == 0 means the
             residual is register allocation only, register search's own job
  stop       why the arm stopped (exhausted = no move left, budget = spent)
  progress   best gradient vs root
  direction  structural key distance (key_distance.canonical, structure=True) of the arm's best source vs
             the root: toward / same / away from the reference body
and an examples file with the root-vs-reference body diff for register-only functions, to read what change
the answer needed and whether a register-search family could make it.

    python3 diagnose.py
"""
import collections
import difflib
import json
import sys
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/refinement-data-20260927/analysis")
from key_distance import REFERENCE, canonical  # noqa: E402
from patterns.commit_provenance import function_definitions  # noqa: E402

EXPERIMENTS = [Path("/home/grant/decomp/experiments/evolvability-trial-20260928"),
               Path("/home/grant/decomp/experiments/evolvability-replication-20260928")]
HERE = Path(__file__).resolve().parent
refs = {}
for path in REFERENCE.rglob("*.c"):
    try:
        for name, text in function_definitions(path.read_text(errors="replace")).items():
            refs.setdefault(name, text)
    except Exception:
        continue

rows = []
for exp in EXPERIMENTS:
    for p in sorted(exp.glob("run-*/**/result.json")):
        r = json.loads(p.read_text())
        if r.get("seed", 0) == 0:
            rows.append(r)
by_fn = collections.defaultdict(dict)
for r in rows:
    by_fn[r["function"]][r["arm"]] = r


def dist(src, name):
    key = canonical(refs[name], None, True) if name in refs else None
    cand = canonical(src, name, True)
    return None if key is None or cand is None else Levenshtein.normalized_distance(cand, key)


residual = collections.Counter()
table = collections.defaultdict(collections.Counter)
examples = []
for name, arms in sorted(by_fn.items()):
    any_arm = next(iter(arms.values()))
    root_src = any_arm["events"][0]["source"]
    root_g = tuple(any_arm["baseline_gradient"] or ())
    kind = "no gradient" if not root_g else ("register-only" if root_g[0] == 0 else "structural")
    residual[kind] += 1
    d_root = dist(root_src, name)
    for arm, r in arms.items():
        t = table[(arm, kind)]
        t["functions"] += 1
        t[f"stop:{r.get('stop')}"] += 1
        best = tuple(r.get("best_compiled_gradient") or ())
        t["exact"] += bool(r.get("exact"))
        t["gradient improved"] += bool(r.get("exact") or (best and root_g and best < root_g))
        d_best = dist(r.get("best_source") or root_src, name)
        if d_root is None or d_best is None:
            t["direction: no reference"] += 1
        else:
            t["direction: " + ("toward" if d_best < d_root - 1e-9 else "away" if d_best > d_root + 1e-9
                               else "same")] += 1
    if kind == "register-only" and name in refs and len(examples) < 12:
        body = function_definitions(root_src).get(name, "")
        diff = "".join(difflib.unified_diff(body.splitlines(True), refs[name].splitlines(True),
                                            "candidate", "reference", n=1))
        examples.append(f"===== {name}  root gradient {list(root_g)}  "
                        f"production stop {arms.get('production', {}).get('stop')}\n{diff}\n")

print("root residual kind:", dict(residual))
for (arm, kind), t in sorted(table.items()):
    print(f"\n{arm:15s} {kind:14s} " + ", ".join(f"{k} {v}" for k, v in sorted(t.items())))
(HERE / "diagnose-examples.txt").write_text("".join(examples))
print(f"\nexamples written: {len(examples)} (diagnose-examples.txt, reference text: keep out of the repo index)")

"""Which operation to try next: an operation-value model learned from the campaign's own recorded attempts.

    sbk1 venv:    python3 -m eval.op_policy build  --db CAMPAIGN.sqlite --out DIR   (features + labels, no compiles)
    policy venv:  python3 -m eval.op_policy train  --out DIR                        (gradient-boosted trees)
    policy venv:  python3 -m eval.op_policy replay --out DIR [--budgets 25,50,100,200]

STATE  = the residual BEFORE the operation: the parent attempt's instruction diff (fault-class counts from
         solver.residual_classes, diagnosed rules from solver.principles.residual_rules, row/distance counts), its
         score and depth.  ACTION = the child's strategy (operation family).
LABEL  = the child is exact or an ANCESTOR of an exact attempt ("on a path to an exact"). Not the score change:
         on the 2026-09-08 campaign (337,211 attempts) flat steps lie on such paths as often as improving ones
         (2.9% vs 3.2%) while worse steps almost never do (12 of 132,715). Training on score deltas would teach the
         policy to skip useful moves (the retention finding of eval/results/evolvability-trial-20260928).
SPLIT  = by function (hash), so replay functions are never seen in training.

REPLAY reallocates the SAME recorded attempts (off-policy, like eval.research_loop replay): an operation becomes
available only after its parent has been explored; the policy picks the next available one. Reported: functions
reaching an exact within each step budget, and steps to first exact, for recorded order, random order (5 seeds) and
the model. It cannot credit branches the campaign never tried, and recorded data reflects the campaign's own choices.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys
from pathlib import Path

FEATURE_CLASSES = ("control_flow", "width", "frame", "layout", "operand", "instructions", "registers")


def family(strategy: str | None) -> str:
    return (strategy or "?").split(":")[0]


def test_split(func_addr) -> bool:
    return int(hashlib.sha256(f"op-policy:{func_addr}".encode()).hexdigest(), 16) % 5 == 0


def state_features(diff: str, score, compiled, depth) -> dict:
    from solver import principles, residual_classes, signals
    diff = diff or ""
    counts = residual_classes.counts(diff) if diff else {}
    rows = [r for r in diff.splitlines() if r[:1] in "+-" and not r.startswith(("+++", "---"))]
    instr, regs = signals.distances(diff) if diff else (0, 0)
    try:
        rules = [r for r, _why in principles.residual_rules(diff)] if diff else []
    except Exception:
        rules = []
    return {"score": float(score or 0.0), "compiled": int(bool(compiled)), "depth": int(depth or 0),
            "rows_minus": sum(r[0] == "-" for r in rows), "rows_plus": sum(r[0] == "+" for r in rows),
            "instr_distance": instr, "reg_distance": regs,
            **{f"class_{c}": int(counts.get(c, 0)) for c in FEATURE_CLASSES}, "rules": rules}


def build(db: Path, out: Path) -> None:
    import sqlite3
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = list(con.execute("select id, parent_attempt_id, func_addr, strategy, compiled, exact, score, iteration "
                            "from attempts"))
    parent = {r[0]: r[1] for r in rows}
    on_path = set()
    for r in rows:
        if r[5]:
            x = r[0]
            while x is not None and x not in on_path:
                on_path.add(x)
                x = parent.get(x)
    parents = sorted({r[1] for r in rows if r[1] is not None})
    feats = {}
    for i in range(0, len(parents), 2000):
        chunk = parents[i:i + 2000]
        q = f"select id, diff_summary, score, compiled, iteration from attempts where id in ({','.join('?' * len(chunk))})"
        for pid, diff, score, compiled, depth in con.execute(q, chunk):
            feats[pid] = state_features(diff, score, compiled, depth)
        print(f"features {min(i + 2000, len(parents))}/{len(parents)}", flush=True)
    by_id = {r[0]: r for r in rows}
    with open(out / "steps.jsonl", "w") as f:
        for aid, pid, func, strategy, compiled, exact, score, depth in rows:
            if pid is None or pid not in feats:
                continue
            p = by_id.get(pid)
            outcome = ("exact" if exact else "nocompile" if not compiled else
                       "improved" if (score or 0) > (p[6] or 0) + 1e-9 else
                       "flat" if abs((score or 0) - (p[6] or 0)) <= 1e-9 else "worse")
            f.write(json.dumps({"id": aid, "parent": pid, "func": func, "action": family(strategy),
                                "state": feats[pid], "on_path": int(aid in on_path), "outcome": outcome,
                                "test": test_split(func)}) + "\n")
    roots = [r[0] for r in rows if r[1] is None or r[1] not in by_id]
    (out / "build.json").write_text(json.dumps({"attempts": len(rows), "steps_with_state": sum(1 for _ in open(out / "steps.jsonl")),
                                                "on_path": len(on_path), "roots": len(roots), "db": str(db)}, indent=1))
    print((out / "build.json").read_text())


def _vectorize(steps, actions, rules):
    import numpy as np
    keys = ["score", "compiled", "depth", "rows_minus", "rows_plus", "instr_distance", "reg_distance",
            *[f"class_{c}" for c in FEATURE_CLASSES]]
    X = np.zeros((len(steps), len(keys) + len(rules) + len(actions)), dtype=np.float32)
    for i, s in enumerate(steps):
        st = s["state"]
        X[i, :len(keys)] = [st[k] for k in keys]
        for r in st["rules"]:
            if r in rules:
                X[i, len(keys) + rules[r]] = 1
        if s["action"] in actions:
            X[i, len(keys) + len(rules) + actions[s["action"]]] = 1
    return X


def train(out: Path) -> None:
    import pickle
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import average_precision_score, roc_auc_score
    steps = [json.loads(line) for line in open(out / "steps.jsonl")]
    tr = [s for s in steps if not s["test"]]
    te = [s for s in steps if s["test"]]
    actions = {a: i for i, a in enumerate(sorted({s["action"] for s in tr}))}
    rules = {r: i for i, r in enumerate(sorted({r for s in tr for r in s["state"]["rules"]}))}
    Xtr, ytr = _vectorize(tr, actions, rules), np.array([s["on_path"] for s in tr])
    Xte, yte = _vectorize(te, actions, rules), np.array([s["on_path"] for s in te])
    # Positives are rare (~2%); balanced class weight keeps the ranking from collapsing to "never on path".
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, class_weight="balanced", random_state=0)
    model.fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    report = {"train_steps": len(tr), "test_steps": len(te), "train_pos": int(ytr.sum()), "test_pos": int(yte.sum()),
              "test_auc": float(roc_auc_score(yte, p)) if 0 < yte.sum() < len(yte) else None,
              "test_avg_precision": float(average_precision_score(yte, p)) if yte.sum() else None,
              "base_rate": float(yte.mean())}
    with open(out / "model.pkl", "wb") as f:
        pickle.dump({"model": model, "actions": actions, "rules": rules}, f)
    (out / "train.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def replay(out: Path, budgets: list[int]) -> None:
    import heapq
    import pickle
    steps = [json.loads(line) for line in open(out / "steps.jsonl") if '"test": true' in line]
    bundle = pickle.load(open(out / "model.pkl", "rb"))
    scores = bundle["model"].predict_proba(_vectorize(steps, bundle["actions"], bundle["rules"]))[:, 1]
    children = collections.defaultdict(list)
    by_func = collections.defaultdict(set)
    for s, v in zip(steps, scores):
        children[s["parent"]].append((s["id"], float(v), s["outcome"] == "exact"))
        by_func[s["func"]].add(s["parent"])
    child_ids = {c[0] for cs in children.values() for c in cs}
    results = collections.defaultdict(list)
    for func, parents in by_func.items():
        roots = [p for p in parents if p not in child_ids]
        if not any(c[2] for p in parents for c in children[p]):
            continue                                # only functions whose recorded tree contains an exact
        for name, key in (("recorded", lambda c: c[0]), ("model", lambda c: -c[1])) + tuple(
                (f"random{seed}", (lambda seed: (lambda c: random.Random(f"{seed}:{c[0]}").random()))(seed))
                for seed in range(5)):
            frontier = []
            for r in roots:
                for c in children[r]:
                    heapq.heappush(frontier, (key(c), c[0], c))
            n, found = 0, None
            while frontier:
                _k, _id, c = heapq.heappop(frontier)
                n += 1
                if c[2]:
                    found = n
                    break
                for cc in children.get(c[0], []):
                    heapq.heappush(frontier, (key(cc), cc[0], cc))
            results[name].append(found)
    names = ["recorded", "model"] + [f"random{s}" for s in range(5)]
    report = {"functions_with_exact_in_tree": len(results["recorded"]), "budgets": {}}
    for b in budgets:
        report["budgets"][str(b)] = {n: sum(1 for x in results[n] if x is not None and x <= b) for n in names}
    for n in names:
        found = sorted(x for x in results[n] if x is not None)
        report[f"median_steps_{n}"] = found[len(found) // 2] if found else None
    rnd = [report["budgets"][str(b)] for b in budgets]
    report["random_mean"] = {str(b): sum(r[f"random{s}"] for s in range(5)) / 5 for b, r in zip(budgets, rnd)}
    (out / "replay.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("build", "train", "replay"))
    ap.add_argument("--db", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--budgets", default="10,25,50,100,200,400")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.db, a.out)
    elif a.cmd == "train":
        train(a.out)
    else:
        replay(a.out, [int(x) for x in a.budgets.split(",")])
    return 0


if __name__ == "__main__":
    sys.exit(main())

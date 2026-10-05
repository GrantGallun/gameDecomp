"""Offline test of measured transition contracts. No compiles.

Question: does a model fitted on observed transitions order a held-out parent's children better than the
order the search actually used? Only children that were actually compiled are ranked, so no outcome is
invented. Folds are by function (sha256 parity, as population-transfer's prior arm). Reported per model:
mean rank of the first improving child, and of the first exact child, among the observed children.

Models (each fitted on the other fold only; families need >= 10 edges from >= 3 functions, else backoff):
  observed  the order the search used (round-robin family streams)
  M0        family improvement rate
  M1        family rate conditioned on the parent's dominant residual axis (backoff to M0)
  M2        M1 plus measured potential: P(an improving grandchild | family applied), from expanded children
"""
import collections
import hashlib
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent


def fold(name):
    return int(hashlib.sha256(name.encode()).hexdigest(), 16) % 2


def dominant(axes):
    if not axes or not sum(axes.values()):
        return "none"
    return max(sorted(axes), key=lambda a: axes[a])


def load():
    worlds = collections.defaultdict(dict)
    for line in (OUT / "nodes.jsonl").open():
        r = json.loads(line)
        worlds[(r["function"], r["arm"])][r["id"]] = r
    return worlds


def edges(worlds):
    """(function, parent, child, grandchildren) for every observed transition."""
    for (function, arm), nodes in worlds.items():
        kids = collections.defaultdict(list)
        for n in nodes.values():
            if n["parent"] is not None:
                kids[n["parent"]].append(n)
        for pid, children in kids.items():
            parent = nodes[pid]
            if "fires" not in parent:
                continue
            children.sort(key=lambda n: int(n["id"].rsplit("/", 1)[1]))      # compile order within the parent
            yield function, arm, parent, children, kids


def improved(parent, child):
    return child["exact"] or (child["compiled"] and child["score"] > parent["score"])


def fit(worlds, train_fold):
    rate = collections.defaultdict(lambda: [0, 0, set()])            # family -> improved, n, functions
    cond = collections.defaultdict(lambda: [0, 0, set()])            # (family, axis) -> ...
    enable = collections.defaultdict(lambda: [0, 0, set()])          # family -> child expanded & a grandchild improved
    for function, _arm, parent, children, kids in edges(worlds):
        if fold(function) != train_fold:
            continue
        axis = dominant(parent.get("axes"))
        for c in children:
            hit = improved(parent, c)
            for table, key in ((rate, c["family"]), (cond, (c["family"], axis))):
                table[key][0] += hit; table[key][1] += 1; table[key][2].add(function)
            if c["id"] in kids:
                grand = kids[c["id"]]
                enable[c["family"]][0] += any(improved(c, g) for g in grand)
                enable[c["family"]][1] += 1
                enable[c["family"]][2].add(function)
    return rate, cond, enable


def supported(cell, n=10, f=3):
    return cell[1] >= n and len(cell[2]) >= f


def score(model, tables, parent, child):
    rate, cond, enable = tables
    fam = child["family"]
    base = rate[fam][0] / rate[fam][1] if supported(rate[fam]) else 0.0
    if model == "M0":
        return base
    c = cond[(fam, dominant(parent.get("axes")))]
    value = c[0] / c[1] if supported(c) else base
    if model == "M1":
        return value
    e = enable[fam]
    return value + (e[0] / e[1] if supported(e, 5, 3) else 0.0) * 0.5


def evaluate(worlds):
    results = {m: {"first_improve": [], "first_exact": []} for m in ("observed", "M0", "M1", "M2")}
    for held in (0, 1):
        tables = fit(worlds, 1 - held)
        for function, _arm, parent, children, _kids in edges(worlds):
            if fold(function) != held or len(children) < 2:
                continue
            for model in results:
                order = children if model == "observed" else sorted(
                    children, key=lambda c: (-score(model, tables, parent, c), int(c["id"].rsplit("/", 1)[1])))
                for key, test in (("first_improve", lambda c: improved(parent, c)), ("first_exact", lambda c: c["exact"])):
                    ranks = [i + 1 for i, c in enumerate(order) if test(c)]
                    if ranks:
                        results[model][key].append(ranks[0] / len(order))      # normalised: 1/n best, 1 worst
    summary = {}
    for model, r in results.items():
        summary[model] = {k: {"cases": len(v), "mean_normalised_rank": round(sum(v) / len(v), 4) if v else None}
                          for k, v in r.items()}
    return summary


def family_table(worlds):
    rate, _cond, enable = fit(worlds, 0)
    r1, _c1, e1 = fit(worlds, 1)
    rows = {}
    for fam in set(rate) | set(r1):
        n = rate[fam][1] + r1[fam][1]
        k = rate[fam][0] + r1[fam][0]
        en = enable[fam][1] + e1[fam][1]
        ek = enable[fam][0] + e1[fam][0]
        rows[fam] = {"edges": n, "functions": len(rate[fam][2] | r1[fam][2]), "improve_rate": round(k / n, 3) if n else None,
                     "expanded_children": en, "enables_improving_grandchild": round(ek / en, 3) if en else None}
    return dict(sorted(rows.items(), key=lambda kv: -kv[1]["edges"]))


def main():
    worlds = load()
    summary = evaluate(worlds)
    table = family_table(worlds)
    (OUT / "offline.json").write_text(json.dumps({"summary": summary, "families": table}, indent=1))
    print(json.dumps(summary, indent=1))
    for fam, row in list(table.items())[:26]:
        print(f"  {fam:30} {row}")


if __name__ == "__main__":
    main()

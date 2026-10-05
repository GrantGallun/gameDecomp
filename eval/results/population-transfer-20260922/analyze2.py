"""Stage-2 arms against stage-1 `expanded`, paired per function. No compiles. Writes analysis2.json."""
import collections
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"


def load(directory):
    rows = {}
    for p in (NATIVE / directory).glob("*.json"):
        r = json.loads(p.read_text())
        rows[(r["function"], r["arm"])] = r
    return rows


def solved(r):
    return bool(r and r.get("exact")) and not r.get("baseline_exact")


def sign_p(k, n):
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else None


def main():
    stage1, stage2 = load("rows"), load("stage2/rows")
    exposed = set(json.loads((OUT / "exposed.json").read_text())["exposed"])
    names = sorted({f for f, _a in stage1})
    result = {}
    for arm in ("routed", "prior"):
        pairs = [(f, stage1.get((f, "expanded")), stage2.get((f, arm))) for f in names]
        pairs = [(f, a, b) for f, a, b in pairs if a and b]
        gains = sorted(f for f, a, b in pairs if solved(b) and not solved(a))
        losses = sorted(f for f, a, b in pairs if solved(a) and not solved(b))
        both = [(f, a, b) for f, a, b in pairs if solved(a) and solved(b)]
        result[arm] = {
            "functions": len(pairs),
            "expanded_exact": sum(solved(a) for _f, a, _b in pairs),
            "arm_exact": sum(solved(b) for _f, _a, b in pairs),
            "gains": gains, "losses": losses,
            "gains_unexposed": [f for f in gains if f not in exposed],
            "sign_test_p_one_sided": sign_p(len(gains), len(gains) + len(losses)),
            "compiles": {"expanded": sum(a.get("compiles", 0) for _f, a, _b in pairs),
                         arm: sum(b.get("compiles", 0) for _f, _a, b in pairs)},
            "compiles_on_common_exacts": {"expanded": sum(a["compiles"] for _f, a, _b in both),
                                          arm: sum(b["compiles"] for _f, _a, b in both)},
            "gain_paths": {f: stage2[(f, arm)].get("best_path_families") for f in gains},
            "best_score_delta_on_nonexact": round(sum(b.get("best_score", 0) - a.get("best_score", 0)
                                                      for _f, a, b in pairs if not solved(a) and not solved(b)), 3),
        }
    owner_edges = collections.defaultdict(lambda: [0, 0, 0, set()])
    for (f, arm), r in stage2.items():
        if arm != "routed" or not r.get("world"):
            continue
        world = json.loads(Path(r["world"]).read_text())["world"]
        nodes = {n["id"]: n for n in world["nodes"]}
        for n in world["nodes"]:
            if n["parent"] is None or not n["family"].startswith("owner:"):
                continue
            parent = nodes[n["parent"]]["verdict"]
            cell = owner_edges[n["family"]]
            cell[0] += 1
            cell[1] += bool(n["verdict"]["compiled"] and n["verdict"]["score"] > parent["score"])
            cell[2] += bool(n["verdict"]["exact"])
            cell[3].add(f)
    result["owner_edges"] = {fam: {"edges": v[0], "improved": v[1], "exact": v[2], "functions": len(v[3])}
                             for fam, v in sorted(owner_edges.items(), key=lambda kv: -kv[1][0])}
    (OUT / "analysis2.json").write_text(json.dumps(result, indent=1))
    for arm in ("routed", "prior"):
        r = result[arm]
        print(f"== {arm} vs expanded on {r['functions']} functions: exact {r['expanded_exact']} -> {r['arm_exact']}, "
              f"gains {len(r['gains'])} losses {len(r['losses'])}, sign p={r['sign_test_p_one_sided']}")
        print(f"   gains: {r['gains']}  (unexposed: {r['gains_unexposed']})\n   losses: {r['losses']}")
        print(f"   compiles {r['compiles']}  on common exacts {r['compiles_on_common_exacts']}  "
              f"best-score delta on non-exact {r['best_score_delta_on_nonexact']}")
        for f, path in r["gain_paths"].items():
            print(f"     {f}: {path}")
    print("owner families (routed arm):")
    for fam, v in result["owner_edges"].items():
        print(f"   {fam:36} edges={v['edges']:4} improved={v['improved']:4} exact={v['exact']:3} functions={v['functions']}")


if __name__ == "__main__":
    main()

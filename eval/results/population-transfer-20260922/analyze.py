"""Read the population run's rows and worlds; no compiles. Writes analysis.json and prints tables.

Four questions, each one a claim the 2026-09-22 Codex work made on 1-13 functions:
  1. Transfer: do the seven new families add exacts over the control repertoire on the population?
  2. Firing: on how many residuals does each family propose anything at all? (silent-decline check)
  3. Enabling edits: how often does a path to exact pass through a step that did not raise the score?
  4. Learnability: are per-family improvement rates stable across disjoint halves of the population?
     This is the premise of the transition planner, whose rows each had one supporting target.
"""
import collections
import hashlib
import json
import math
from pathlib import Path
import random
import sys

OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from solver import signals  # noqa: E402

NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
NEW = set(FROZEN["new_families"])
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def main():
    rows = [json.loads(p.read_text()) for p in sorted((NATIVE / "rows").glob("*.json"))]
    by = collections.defaultdict(dict)
    for r in rows:
        by[r["function"]][r["arm"]] = r
    paired = {f: a for f, a in by.items() if "control" in a and "expanded" in a}
    result = {"functions_with_both_arms": len(paired)}

    # 1. Transfer ---------------------------------------------------------------------------------
    def solved(r):
        return bool(r.get("exact")) and not r.get("baseline_exact")
    table = collections.Counter()
    for f, a in paired.items():
        table[(solved(a["control"]), solved(a["expanded"]))] += 1
    gains = sorted(f for f, a in paired.items() if solved(a["expanded"]) and not solved(a["control"]))
    losses = sorted(f for f, a in paired.items() if solved(a["control"]) and not solved(a["expanded"]))
    both = sorted(f for f, a in paired.items() if solved(a["control"]) and solved(a["expanded"]))
    exposed = set(json.loads((OUT / "exposed.json").read_text())["exposed"])
    result["transfer"] = {
        "control_exact": sum(solved(a["control"]) for a in paired.values()),
        "expanded_exact": sum(solved(a["expanded"]) for a in paired.values()),
        "gains": gains, "losses": losses, "both": both,
        "unexposed": {"functions": sum(f not in exposed for f in paired),
                      "control_exact": sum(solved(a["control"]) for f, a in paired.items() if f not in exposed),
                      "expanded_exact": sum(solved(a["expanded"]) for f, a in paired.items() if f not in exposed),
                      "gains": [f for f in gains if f not in exposed], "losses": [f for f in losses if f not in exposed]},
        "exposed_gains": [f for f in gains if f in exposed],
        "baseline_exact": sorted(f for f, a in paired.items() if a["expanded"].get("baseline_exact")),
        "baseline_refused": sorted(f for f, a in paired.items() if a["expanded"].get("baseline_compiled") is False),
        "no_target_object": sorted(f for f, a in paired.items() if a["expanded"].get("status") == "no-target-object"),
        "compiles": {arm: sum(a[arm].get("compiles", 0) for a in paired.values()) for arm in ("control", "expanded")},
        "gain_paths": {f: paired[f]["expanded"].get("best_path_families") for f in gains},
        "infrastructure_errors": sum(a[arm].get("infrastructure_errors", 0) for a in paired.values()
                                     for arm in ("control", "expanded")),
    }
    # Sign test on discordant pairs: is expanded > control beyond chance?
    k, n = len(gains), len(gains) + len(losses)
    result["transfer"]["sign_test_p_one_sided"] = (
        sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else None)

    # 2. Firing census at the root ----------------------------------------------------------------
    searchable = [a["expanded"] for a in paired.values() if "root_census" in a["expanded"]]
    fire = collections.Counter()
    for r in searchable:
        for fam, count in r["root_census"].items():
            if count:
                fire[fam] += 1
    result["firing"] = {"searchable_roots": len(searchable),
                        "zero_variant_roots": sorted(r["function"] for r in searchable if not r["root_census"]),
                        "functions_fired": dict(fire.most_common()),
                        "new_families_never_fired": sorted(NEW - set(fire))}

    # Residual shape at the root, and which shapes the search closes -------------------------------
    shapes = collections.Counter()
    closed_by_shape = collections.Counter()
    zero_shapes = collections.Counter()
    for f, a in paired.items():
        r = a["expanded"]
        if "root_census" not in r:
            continue
        world = json.loads(Path(r["world"]).read_text())["world"]
        root = world["nodes"][0]["verdict"]
        s = signals.analyse(root.get("diff") or "", root["score"], root["exact"], root["compiled"])
        profile = {axis: int(getattr(s, axis)) for axis in AXES}
        total = sum(profile.values())
        dominant = max(profile, key=profile.get) if total else "none"
        band = "<=4 faults" if total <= 4 else "<=12 faults" if total <= 12 else ">12 faults"
        key = f"{dominant}|{band}"
        shapes[key] += 1
        if solved(r) or solved(a["control"]):
            closed_by_shape[key] += 1
        if not r["root_census"]:
            zero_shapes[key] += 1
        r["_profile"] = profile
    result["root_shapes"] = {k: {"n": v, "closed": closed_by_shape[k], "zero_variant": zero_shapes[k]}
                             for k, v in shapes.most_common()}

    # 3 + 4. Edges: improvement rates per family, enabling steps on exact paths --------------------
    edges = collections.defaultdict(list)          # family -> [(function, improved, exact)]
    exact_paths = []
    for f, a in paired.items():
        for arm in ("control", "expanded"):
            r = a[arm]
            if not r.get("world"):
                continue
            world = json.loads(Path(r["world"]).read_text())["world"]
            nodes = {n["id"]: n for n in world["nodes"]}
            for n in world["nodes"]:
                if n["parent"] is None:
                    continue
                parent = nodes[n["parent"]]["verdict"]
                v = n["verdict"]
                if arm == "expanded" or n["family"] not in NEW:
                    edges[(arm, n["family"])].append(
                        (f, bool(v["compiled"] and v["score"] > parent["score"]), bool(v["exact"])))
            if solved(r):
                path, cursor = [], r["best_id"]
                while cursor != "root":
                    node = nodes[cursor]
                    parent = nodes[node["parent"]]["verdict"]
                    path.append({"family": node["family"], "from": parent["score"], "to": node["verdict"]["score"],
                                 "exact": node["verdict"]["exact"]})
                    cursor = node["parent"]
                path.reverse()
                # A non-final step that did not raise the score is an enabling step.
                enabling = [p for p in path[:-1] if p["to"] <= p["from"]]
                exact_paths.append({"function": f, "arm": arm, "steps": len(path), "families": [p["family"] for p in path],
                                    "enabling_steps": len(enabling), "final_step_gain": round(path[-1]["to"] - path[-1]["from"], 3)
                                    if path else None})
    result["exact_paths"] = exact_paths
    result["exact_path_summary"] = {
        "paths": len(exact_paths),
        "multi_step": sum(p["steps"] > 1 for p in exact_paths),
        "with_enabling_step": sum(p["enabling_steps"] > 0 for p in exact_paths)}

    family_rates = {}
    for (arm, fam), items in edges.items():
        if arm != "expanded":
            continue
        functions = {f for f, _i, _e in items}
        improved = sum(i for _f, i, _e in items)
        family_rates[fam] = {"edges": len(items), "functions": len(functions), "improved": improved,
                             "improve_rate": round(improved / len(items), 3), "ci95": wilson(improved, len(items)),
                             "exact_children": sum(e for _f, _i, e in items)}
    result["family_rates"] = dict(sorted(family_rates.items(), key=lambda kv: -kv[1]["edges"]))

    # Split-half stability of per-family, per-function improvement rates (functions are the unit).
    fams = [fam for fam, v in family_rates.items() if v["functions"] >= 6]
    per_fn = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for (arm, fam), items in edges.items():
        if arm == "expanded" and fam in fams:
            for f, i, _e in items:
                per_fn[fam][f][0] += i
                per_fn[fam][f][1] += 1
    names = sorted(paired)
    rng = random.Random(20260922)
    correlations = []
    for _ in range(200):
        rng.shuffle(names)
        half = set(names[: len(names) // 2])
        xs, ys = [], []
        for fam in fams:
            a = [k / n for f, (k, n) in per_fn[fam].items() if f in half]
            b = [k / n for f, (k, n) in per_fn[fam].items() if f not in half]
            if a and b:
                xs.append(sum(a) / len(a))
                ys.append(sum(b) / len(b))
        if len(xs) >= 3:
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
            sy = math.sqrt(sum((y - my) ** 2 for y in ys))
            if sx and sy:
                correlations.append(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy))
    correlations.sort()
    result["split_half"] = {"families": len(fams), "draws": len(correlations),
                            "median_r": round(correlations[len(correlations) // 2], 3) if correlations else None,
                            "p05_r": round(correlations[int(len(correlations) * 0.05)], 3) if correlations else None}

    (OUT / "analysis.json").write_text(json.dumps(result, indent=1))
    t = result["transfer"]
    print(f"paired functions: {len(paired)}")
    print(f"exact  control={t['control_exact']}  expanded={t['expanded_exact']}  gains={len(gains)} losses={len(losses)}"
          f"  sign-test p={t['sign_test_p_one_sided']}")
    print(f"gains: {gains}\nlosses: {losses}\nboth: {both}")
    print(f"UNEXPOSED (transfer) {t['unexposed']}\nexposed gains (development, not transfer): {t['exposed_gains']}")
    print(f"baseline refused={len(t['baseline_refused'])} baseline exact={len(t['baseline_exact'])} "
          f"no target={len(t['no_target_object'])} compiles={t['compiles']} infra errors={t['infrastructure_errors']}")
    fr = result["firing"]
    print(f"\nsearchable roots {fr['searchable_roots']}, zero-variant roots {len(fr['zero_variant_roots'])}")
    print("new families never fired:", fr["new_families_never_fired"])
    for fam, n in fr["functions_fired"].items():
        mark = "*" if fam in NEW else " "
        print(f"  {mark}{fam:22} fires on {n:3} roots")
    print("\nroot shapes (dominant|size): n closed zero-variant")
    for k, v in result["root_shapes"].items():
        print(f"  {k:28} {v['n']:4} {v['closed']:4} {v['zero_variant']:4}")
    print("\nexact paths:", result["exact_path_summary"])
    print("split-half family-rate stability:", result["split_half"])
    print("\nfamily improve rates (expanded arm):")
    for fam, v in result["family_rates"].items():
        mark = "*" if fam in NEW else " "
        print(f"  {mark}{fam:22} edges={v['edges']:5} fns={v['functions']:4} improve={v['improve_rate']:.3f} "
              f"ci={v['ci95']} exact_children={v['exact_children']}")


if __name__ == "__main__":
    main()

"""Fire test of solver.branch_shape on real residuals: the module's own gated variants from each best node,
compiled with the function's recipe, greedy chain up to 3 rounds (next round starts from the best improvement).

    python3 fire_module.py [FUNCTION ...]   (default: every unsolved function) -> fire_module.json"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import branch_shape  # noqa: E402

import census  # noqa: E402
import fire  # noqa: E402


def run(name, rounds=3):
    row = json.loads((census.E / "rows" / f"{name}--routed.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
    source, verdict = node["source"], node["verdict"]
    score0 = verdict["score"]
    log = {"function": name, "start": score0, "rounds": []}
    cur_src, cur_diff, cur_score = source, verdict.get("diff") or "", score0
    for _ in range(rounds):
        cands = list(branch_shape.variants(cur_src, name, cur_diff, verdict))
        if not cands:
            break
        results = []
        for label, kind, cand in cands:
            score, dump, _log = fire.build(name, cand)
            diff_path = fire.WORK / name / "nonmatchings" / name / f"{name}_diff"
            results.append((score if score is not None else -1, label, kind, cand,
                            diff_path.read_text() if diff_path.exists() else ""))
        log["rounds"].append([(r[1], r[0]) for r in results])
        best = max(results, key=lambda r: r[0])
        if best[0] <= cur_score:
            break
        cur_score, cur_src, cur_diff = best[0], best[3], best[4]
        if cur_score >= 100.0:
            break
    log["end"] = cur_score
    log["source"] = cur_src
    return log


def main(names):
    if not names:
        d = json.loads((census.HERE / "census.json").read_text())
        names = [r["function"] for r in d["rows"]]
    out = []
    for n in names:
        try:
            r = run(n)
        except Exception as exc:  # noqa: BLE001
            print(n, "error", exc)
            continue
        if r["rounds"]:
            print(f"{n[:40]:40s} {r['start']:7.3f} -> {r['end']:7.3f}  {r['rounds']}", flush=True)
            out.append(r)
    (census.HERE / ("fire_module.json" if len(names) > 10 else "fire_module_named.json")).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])

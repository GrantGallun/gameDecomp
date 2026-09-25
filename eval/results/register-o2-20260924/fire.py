"""Fire test of branch_shape.o1_register_saved alone on the -O1 population functions with the register signature.
Each best node (restart round 3) -> the family's candidates, compiled with the function's recorded recipe in the
branch-layout copied workspaces; greedy, 2 rounds. Not population evidence.

    python3 fire.py [FUNCTION ...] -> fire.json"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(HERE.parent / "branch-layout-20260924"))
from solver import branch_shape  # noqa: E402
import census  # noqa: E402
import fire as bl_fire  # noqa: E402

O1 = ["__osPfsRWInode", "__osPfsSelectBank", "__osResetGlobalIntMask", "__osSetGlobalIntMask", "osPfsIsPlug",
      "osPiRawStartDma", "osStartThread"]


def run(name, rounds=2):
    row = json.loads((census.E / "rows" / f"{name}--routed.json").read_text())
    world = json.loads(Path(row["world"]).read_text())["world"]
    node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
    verdict = node["verdict"]
    src, diff, score = node["source"], verdict.get("diff") or verdict.get("raw_diff") or "", verdict["score"]
    log = {"function": name, "start": score, "rounds": []}
    for _ in range(rounds):
        cands = list(branch_shape.o1_register_saved(src, name, diff, verdict.get("compiler_recipe")))
        if not cands:
            break
        results = []
        for label, cand in cands:
            s, _dump, _log = bl_fire.build(name, cand)
            d = bl_fire.WORK / name / "nonmatchings" / name / f"{name}_diff"
            results.append((s if s is not None else -1, label, cand, d.read_text() if d.exists() else ""))
        log["rounds"].append([(r[1], r[0]) for r in results])
        best = max(results, key=lambda r: r[0])
        if best[0] <= score:
            break
        score, src, diff = best[0], best[2], best[3]
        if score >= 100.0:
            break
    log["end"], log["source"] = score, src
    return log


def main(names):
    out = []
    for n in names or O1:
        r = run(n)
        print(f"{n:28s} {r['start']:7.3f} -> {r['end']:7.3f}  {r['rounds']}", flush=True)
        out.append(r)
    (HERE / "fire.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])

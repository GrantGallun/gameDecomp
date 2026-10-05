"""What does uopt record for finishCurrentRdpTask's locals, before and after inlining temp_v0?"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/alloc-inverter-20260923")
from solver import alloc_inverter, uopt_diagnosis, uopt_trace  # noqa: E402
import run  # noqa: E402

HERE = Path(__file__).resolve().parent
NAME = sys.argv[1] if len(sys.argv) > 1 else "finishCurrentRdpTask"


def main():
    row = next(r for p in (run.E / "restart-round3-20260923/rows").glob("*.json")
               if (r := json.loads(p.read_text())).get("function") == NAME)
    world = json.loads(Path(row["world"]).read_text())["world"]
    src = next(n for n in world["nodes"] if n["id"] == row["best_id"])["source"]
    repo = run.workspace(NAME) or next((run.E / d / "ws" / NAME / a) for d in ("restart-round3-20260923",)
                                       for a in ("routed", "locality") if (run.E / d / "ws" / NAME / a).exists())
    print("workspace", repo, "locals", alloc_inverter.local_offsets(src, NAME))
    variants = {"best": src}
    if len(sys.argv) > 2:
        variants["inline"] = alloc_inverter._inline(src, NAME, sys.argv[2])
    for tag, text in variants.items():
        texts = uopt_diagnosis.traced_compile(repo / "nonmatchings" / NAME, repo, text, run.TRACE_CC, NAME)
        proc = uopt_trace.join(texts["level5"], texts["level6"])[NAME]
        outcome = {d.piece: d.outcome for d in proc.decisions}
        print("==", tag)
        for lr, r in sorted(proc.ranges.items()):
            print(" ", lr, r.kind, r.offset, "colour", r.color, "adjsave", r.adjsave, "outcome", outcome.get(lr),
                  "node", r.node)
        for line in texts["level5"].splitlines():
            if re.search(r"isvar", line):
                print("  L5|", line[:160])
        (HERE / f"trace_{NAME}_{tag}.txt").write_text(texts["level5"] + "\n=====\n" + texts["level6"])


if __name__ == "__main__":
    main()

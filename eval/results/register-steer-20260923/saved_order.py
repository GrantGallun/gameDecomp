"""The five 99.936 siblings: which ranges hold s2/s3, their priorities, and which C variables they are."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from steer import mapping  # noqa: E402
from eval.allocator_rules import span, units  # noqa: E402
from solver import uopt_trace  # noqa: E402

SIBLINGS = ["drawRaceSplitscreenSelectOption2Frame", "drawRaceSplitscreenSelectOption4Frame",
            "drawCharacterSelectCoursePreviewPanel2", "drawCharacterSelectCoursePreviewPanel6",
            "drawCharacterSelectCoursePreviewPanel8"]
REG = {14 + i: f"s{i}" for i in range(8)}
census = {c["function"]: c for c in json.loads((HERE / "census.json").read_text())}
for name in SIBLINGS:
    c = census[name]
    proc = uopt_trace.join(c["trace"]["level5"], c["trace"]["level6"])[name]
    names = mapping(name, c["best_source"], proc)
    outcome = {d.piece: d.outcome for d in proc.decisions}
    order = [d.piece for d in proc.decisions if d.outcome != "not_colored"]
    rows = []
    for r in proc.ranges.values():
        if r.color in (16, 17):
            rows.append({"var": names.get((r.kind, r.offset)), "kind": r.kind, "offset": r.offset, "lr": r.lr,
                         "reg": REG[r.color], "adjsave": round(r.adjsave, 3), "span": span(r),
                         "save": round(r.adjsave * units(span(r)), 2), "outcome": outcome.get(r.lr),
                         "order": order.index(r.lr) if r.lr in order else None})
    zero_inits = [m.group(1) for m in re.finditer(r"^\s*(\w+)\s*=\s*0\s*;", c["best_source"], re.M)]
    print("==", name, "zero-initialised in source order:", zero_inits)
    for row in sorted(rows, key=lambda r: r["reg"]):
        print("   ", row)

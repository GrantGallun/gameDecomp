"""Show prologues of H17 miss examples to separate instrument faults from rule faults."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
rows = json.loads((HERE / "vreg_frames.json").read_text())["rows"]


def show(r, n=14):
    dump = (REPO / "nonmatchings" / r["function"] / "target_object_dump_normalized.s").read_text(errors="replace")
    lines = [l.strip() for l in dump.splitlines() if l.strip()]
    sp = [l for l in lines if "sp" in l]
    print("==", r["function"], {k: r[k] for k in ("frame", "saved_int", "saved_float", "outgoing", "area",
                                                    "mem_depth", "vreg_depth")})
    print("   ", " | ".join(sp[:n]))


groups = {
    "no locals, area 8": [r for r in rows if r["mem_depth"] == 0 and r["vreg_depth"] == 0 and r["area"] == 8],
    "vreg only, area 8": [r for r in rows if r["mem_depth"] == 0 and r["vreg_depth"] > 0 and r["area"] == 8],
    "mem, area < F0": [r for r in rows if r["mem_depth"] > 0 and 0 <= r["area"] < ((r["mem_depth"] + 7) & ~7)],
    "instrument failure": [r for r in rows if r["area"] < 0],
}
for name, group in groups.items():
    print("#####", name, len(group))
    for r in group[:4]:
        show(r)

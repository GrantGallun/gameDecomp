"""Read-only: why the derived rule proposes nothing on the drop_mask exacts."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from signatures import load, signatures  # noqa: E402

for function, arm, world in load():
    if function not in ("randomNextMain", "drawCharacterSelectCourseExitPopup") or arm != "routed":
        continue
    nodes = {n["id"]: n for n in world["nodes"]}
    for n in world["nodes"]:
        if n["family"] == "owner:drop_mask" and n["verdict"]["exact"]:
            p = nodes[n["parent"]]
            print("==", function, "parent", p["id"])
            for sig in signatures(p["verdict"]["diff"], p["verdict"].get("source_attribution")):
                if sig[0].startswith(("extra:andi", "field", "opcode")) or "andi" in (sig[4] or ""):
                    print("  ", sig[0], "line", sig[2], "|", sig[3], "|", sig[4])
            lines = p["source"].split("\n")
            print("   mask lines:", [(i + 1, l.strip()) for i, l in enumerate(lines) if "0xFF" in l or "& 255" in l][:4])
            print("   diff andi:", [l for l in p["verdict"]["diff"].splitlines() if "andi" in l][:4])

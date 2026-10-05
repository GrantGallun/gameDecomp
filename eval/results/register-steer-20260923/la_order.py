"""Where var_s1's initial address is set decides ugen's order of the two `la`s; try each placement."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ugen_view import ugen_assembly  # noqa: E402

RUN = Path.home() / "decomp/experiments/gated-population-20260923"
NAME = "waitCourseSelectRecordsClose"
src = next(p["source"] for p in json.loads((HERE / "probes-callback.json").read_text())
           if p["label"] == "callback:via_camera_global")
init = "        var_s1 = D_801121E0;\n"
assert init in src
without = src.replace(init, "", 1)
spots = {
    "before_var_s0": ("    var_s0 = 0;\n", "    var_s1 = D_801121E0;\n    var_s0 = 0;\n"),
    "after_var_s0": ("    var_s0 = 0;\n", "    var_s0 = 0;\n    var_s1 = D_801121E0;\n"),
    "top_of_function": ("    if (gCourseSelectSubmenuState == 2) {\n",
                        "    var_s1 = D_801121E0;\n    if (gCourseSelectSubmenuState == 2) {\n"),
}
out = []
for label, (anchor, repl) in spots.items():
    cand = without.replace(anchor, repl, 1)
    asm = ugen_assembly(NAME, cand, RUN)
    body = asm[asm.index(f"{NAME}:"):]
    las = re.findall(r"^\s+la\s+(\w+),\s*(\w+)", body, re.M)
    print(f"{label:18} la order: {las}")
    out.append({"function": NAME, "label": f"la_order:{label}", "source": cand})
(HERE / "probes-la.json").write_text(json.dumps(out, indent=1))

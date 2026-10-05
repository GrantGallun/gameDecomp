"""Loop shapes for waitCourseSelectRecordsClose: which make ugen emit `la s2,gCurrentMenuCameraObject` first?"""
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
begin = src.index("    var_s0 = 0;\n")
end = src.index("    updateCallbackTasks();")
LOOP = src[begin:end]
print(LOOP)
reads = "".join(l + "\n" for l in LOOP.splitlines() if "if (!var_s0);" in l)
SHAPES = {
    "for_both_inits": ("    for (var_s0 = 0, var_s1 = D_801121E0; var_s0 < (s32) gPlayerCount; var_s0++) {\n"
                       "        gCurrentMenuCameraObject = var_s1;\n        gCurrentMenuCameraObject->update();\n"
                       + reads.replace("            ", "        ") +
                       "        var_s1 = (RaceCamera *)((unsigned char *)var_s1 + 0xB0);\n    }\n"),
    "init_in_guard_after_test": LOOP.replace("        var_s1 = D_801121E0;\n        do {\n",
                                             "        do {\n", 1).replace(
        "    if ((s32) gPlayerCount > 0) {\n", "    var_s1 = D_801121E0;\n    if ((s32) gPlayerCount > 0) {\n", 1),
    "camera_first_in_body": LOOP.replace("            gCurrentMenuCameraObject = var_s1;\n",
                                         "            gCurrentMenuCameraObject = var_s1;\n", 1),
    "index_form": ("    var_s0 = 0;\n    if ((s32) gPlayerCount > 0) {\n        do {\n"
                   "            gCurrentMenuCameraObject = &D_801121E0[var_s0];\n"
                   "            gCurrentMenuCameraObject->update();\n" + reads +
                   "            var_s0 += 1;\n        } while (var_s0 < (s32) gPlayerCount);\n    }\n"),
}
out = []
for label, loop in SHAPES.items():
    cand = src[:begin] + loop + src[end:]
    try:
        asm = ugen_assembly(NAME, cand, RUN)
    except Exception as exc:                                        # noqa: BLE001
        print(f"{label:26} does not compile: {str(exc)[-120:]}")
        continue
    body = asm[asm.index(f"{NAME}:"):]
    las = [(r, s) for r, s in re.findall(r"^\s+la\s+(\w+),\s*(\w+)", body, re.M) if s != "updateCourseSelectCourseDetailsMenu"]
    print(f"{label:26} la order: {las}")
    out.append({"function": NAME, "label": f"shape:{label}", "source": cand})
(HERE / "probes-shapes.json").write_text(json.dumps(out, indent=1))

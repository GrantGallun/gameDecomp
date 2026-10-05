"""Spellings of the indirect call in waitCourseSelectRecordsClose: which make ugen take two more temporaries?"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ugen_view import ugen_assembly  # noqa: E402

RUN = Path.home() / "decomp/experiments/gated-population-20260923"
NAME = "waitCourseSelectRecordsClose"
base = next(p["source"] for p in json.loads((HERE / "probes-steer2.json").read_text()) if p["label"] == "steer:loopread+3:var_s0")
CALL = "            var_s1->update();\n"
assert CALL in base
VARIANTS = {
    "as_is": CALL,
    "explicit_deref": "            (*var_s1->update)();\n",
    "cast_call": "            ((void (*)(void))var_s1->update)();\n",
    "via_camera_global": "            gCurrentMenuCameraObject->update();\n",
    "deref_camera_global": "            (*gCurrentMenuCameraObject->update)();\n",
}
out = []
for label, call in VARIANTS.items():
    src = base.replace(CALL, call, 1)
    try:
        asm = ugen_assembly(NAME, src, RUN)
    except Exception as exc:                                # noqa: BLE001
        print(label, "FAILED", str(exc)[:200])
        continue
    body = asm[asm.index(f"{NAME}:"):]
    temps = [m.group(1) for m in re.finditer(r"^\s+\w+\s+(t\d)\b", body, re.M)]
    print(f"{label:22} temporaries written in ugen order: {temps}")
    out.append({"function": NAME, "label": f"callback:{label}", "source": src})
(HERE / "probes-callback.json").write_text(json.dumps(out, indent=1))

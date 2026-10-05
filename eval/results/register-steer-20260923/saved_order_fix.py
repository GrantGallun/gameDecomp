"""Orderings of the tileIndex/offset initialisation: which make ugen emit tileIndex's `move s2,zero` first?"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ugen_view import ugen_assembly  # noqa: E402

RUN = Path.home() / "decomp/experiments/gated-population-20260923"
SIBLINGS = ["drawRaceSplitscreenSelectOption2Frame", "drawRaceSplitscreenSelectOption4Frame",
            "drawCharacterSelectCoursePreviewPanel2", "drawCharacterSelectCoursePreviewPanel6",
            "drawCharacterSelectCoursePreviewPanel8"]
census = {c["function"]: c for c in json.loads((HERE / "census.json").read_text())}
BLOCK = re.compile(r"(?P<ind>[ \t]*)if \(\(1\)\) \{\n[ \t]*(?P<idx>\w+) = 0;\n[ \t]*i = 0x80;\n[ \t]*\}\n[ \t]*offset = 0;\n")


def shapes(src):
    m = BLOCK.search(src)
    if not m:
        return {}
    ind, idx = m.group("ind"), m.group("idx")
    forms = {
        "unwrapped": f"{ind}{idx} = 0;\n{ind}i = 0x80;\n{ind}offset = 0;\n",
        "offset_first": f"{ind}offset = 0;\n{ind}{idx} = 0;\n{ind}i = 0x80;\n",
        "index_after_offset": f"{ind}i = 0x80;\n{ind}offset = 0;\n{ind}{idx} = 0;\n",
        "wrapped_offset_inside": f"{ind}if ((1)) {{\n{ind}    {idx} = 0;\n{ind}    i = 0x80;\n{ind}    offset = 0;\n{ind}}}\n",
        "chained": f"{ind}{idx} = offset = 0;\n{ind}i = 0x80;\n",
    }
    return {k: src[:m.start()] + v + src[m.end():] for k, v in forms.items()}


out = []
for name in SIBLINGS:
    best = census[name]["best_source"]
    for label, src in shapes(best).items():
        try:
            asm = ugen_assembly(name, src, RUN)
        except Exception:                                           # noqa: BLE001
            print(f"{name[:34]:34} {label:22} does not compile")
            continue
        body = asm[asm.index(f"{name}:"):]
        moves = [re.sub(r"\s+", " ", l.strip()) for l in body.splitlines() if re.match(r"\s+move\s+s[1-7],\s*zero", l)]
        print(f"{name[:34]:34} {label:22} {moves}")
        out.append({"function": name, "label": f"saved_fix:{label}", "source": src})
(HERE / "probes-saved-fix.json").write_text(json.dumps(out, indent=1))

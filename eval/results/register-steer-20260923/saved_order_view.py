"""-S order of the `move sX, zero` pair for one sibling, before and after raising `offset` past range 28."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ugen_view import ugen_assembly  # noqa: E402

RUN = Path.home() / "decomp/experiments/gated-population-20260923"
NAME = "drawRaceSplitscreenSelectOption2Frame"
best = next(c for c in json.loads((HERE / "census.json").read_text()) if c["function"] == NAME)["best_source"]
m = re.search(r"^([ \t]*)offset\s*=\s*0\s*;[ \t]*\n", best, re.M)
raised = best[:m.end()] + (m.group(1) + "if (!offset);\n") * 4 + best[m.end():]
print("source around the inits:\n", best[max(0, m.start() - 300):m.end() + 200])
out = []
for label, src in (("best", best), ("offset+4", raised)):
    asm = ugen_assembly(NAME, src, RUN)
    body = asm[asm.index(f"{NAME}:"):]
    moves = [l.strip() for l in body.splitlines() if re.match(r"\s+move\s+s[0-7],\s*zero", l)]
    print(f"{label:10} ugen order of zero moves: {moves}")
    out.append({"function": NAME, "label": f"saved:{label}", "source": src})
(HERE / "probes-saved.json").write_text(json.dumps(out, indent=1))

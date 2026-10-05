"""Measurement check for H16b: how many live-range pieces does x (offset -4) have, and which one moved?"""
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval import allocator_interventions as ai  # noqa: E402
from eval.allocator_rules import span, units  # noqa: E402
from solver import uopt_trace  # noqa: E402
import probe_b  # noqa: E402


def pieces(src):
    command = ai.recipe(probe_b.TREE)
    _o, l5 = ai.compile_traced(probe_b.TREE, command, src, 5)
    _o, l6 = ai.compile_traced(probe_b.TREE, command, src, 6)
    proc = uopt_trace.join(l5, l6)["syn_f"]
    return [(lr, r.adjsave, round(r.adjsave * units(span(r)), 3) if r.adjsave is not None else None, span(r), r.color)
            for lr, r in sorted(proc.ranges.items()) if r.kind == "M" and r.offset == -4]


for e in ("5", "n + 1"):
    src = ai.HEADER + probe_b.BODY.replace("{E}", e)
    print(e, "base ", pieces(src.replace("{P}", "")))
    print(e, "probe", pieces(src.replace("{P}", "        if (!x);\n")))

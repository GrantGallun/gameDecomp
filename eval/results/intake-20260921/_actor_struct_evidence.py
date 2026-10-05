"""The actor struct is defined in the same .c the function comes from. What does it give the draft?

`renderRaceCourseTripleParticle` -> `src/race/ui/race_ui_effects.c` defines `RaceUiTripleParticleActor`,
and the draft uses `arg0->matrixDirty`, `arg0->pos`. If the file's definition has those members at offsets
the binary agrees with, then the definition IS the evidence a repair needs -- it is not invention, it is
the project's own record of the type.

If it is unrelated (a different struct with a colliding name, or members the binary's accesses contradict),
then it is exactly the "invented layout" the project forbids and the class is out of tooling's reach.

This prints, for three of the states: the source definition, the draft's member accesses, and the assembly
accesses on that parameter, so the three can be compared rather than assumed to agree.

READ-ONLY. Note what this is NOT: it is not feeding the reference body to the solver. Reading a project
header or a sibling TU's type declaration is what `header_variant` and `opaque_variant` already do; what
would be contamination is reading a function BODY out of the reference decomp. `src/**` here IS the
reference decomp's own source, so a definition lifted from it must be treated as reference-derived, and the
question this asks is only whether the offsets agree with the binary -- not whether to use it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, workspace                          # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"

CASES = (("renderRaceCourseTripleParticle", "RaceUiTripleParticleActor",
          "src/race/ui/race_ui_effects.c"),
         ("renderPickupShardParticle", "PickupShardParticleActor",
          "src/race/course/race_course_props_and_pickups.c"),
         ("drawTrainingCourseLessonEndMenu", "TrainingCourseUiActor",
          "src/menu/training/training_course_ui.c"))

conn = sqlite3.connect(str(KB))
try:
    for function, type_name, source_file in CASES:
        print("=" * 78)
        print(f"{function}   ({type_name})")
        print("=" * 78)
        path = REPO / source_file
        if not path.is_file():
            print(f"  {source_file} is missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"struct\s+" + re.escape(type_name) + r"\s*\{(?P<body>[^}]*)\}\s*;", text)
        print(f"  definition in {source_file}:")
        if match:
            for line in match.group(0).splitlines()[:24]:
                print(f"     {line.strip()[:96]}")
        else:
            print("     (no body found -- only a forward declaration)")

        context, why = build_context(REPO, function, conn=conn)
        if context is None:
            print(f"  build_context: {why}")
            continue
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""

        header = next((ln for ln in current.splitlines() if function + "(" in ln), "")
        print(f"\n  the draft's signature: {header.strip()[:100]}")
        members = sorted({m.group(2) for m in
                          re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)", current)})
        print(f"  members the draft touches: {members[:12]}")

        asm = workspace.target_asm(workspace.bootstrap(REPO, function), function)
        analysis, _rows = compile_obligations.analyse(asm)
        param_accesses = [a for a in analysis.accesses.values()
                          if a.address and a.address.kind == "address"
                          and str(a.address.name).startswith("param")]
        print(f"  the BINARY's accesses on parameters "
              f"({len(param_accesses)}, this is the offset evidence):")
        for access in sorted(param_accesses, key=lambda a: (a.address.name, a.address.offset))[:14]:
            print(f"     {access.address.name} +0x{access.address.offset:X} width {access.width} "
                  f"{access.opcode}")
        print(f"  binary offsets observed: "
              f"{sorted({a.address.offset for a in param_accesses})}")
        print()
finally:
    conn.close()

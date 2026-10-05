"""Why do several recoveries come back `project-authority` instead of corroborated?

`project-authority` means `parameter_index` returned None, so no binary offsets were looked up and the
corroboration gate never ran. That is exactly the distinction this pass is defensible ON, so a silent
fallthrough to it is a defect: it makes an unverified recovery look like a verified one in every summary
that counts recoveries.

Two candidate causes, and the text settles it: the signature regex not matching, or the type genuinely not
appearing in the parameter list (a LOCAL of that type, which has no `param<i>` evidence at all).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                              # noqa: E402
from solver import source_type_declarations as std                         # noqa: E402

REPO = Path.home() / "decomp/sbk1"
NAMES = ("resolveRaceCourseSurfaceCollisionWithNormal", "enqueueSoundEffect",
         "initRelocatableHeap", "initRaceCoursePropModels")

conn = sqlite3.connect(str(Path.home() / "decomp/kb-sbk1.sqlite"))
try:
    for name in NAMES:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        source = context.candidate or ""
        print(f"{name}")
        signature = std.parameter_names(source)
        print(f"   parameter_names() -> {signature}")
        match = re.search(r"(?m)^[A-Za-z_][^;\n]*\((?P<params>[^)]*)\)\s*\{", source)
        print(f"   signature regex matched: {bool(match)}")
        if match:
            print(f"      params text: {match.group('params')[:90]!r}")
        first = next((ln for ln in source.splitlines() if name + "(" in ln), "")
        print(f"   the draft's own signature line: {first.strip()[:96]}")
        for type_name in std.referenced_types(source, name):
            index = std.parameter_index(source, type_name)
            where = "PARAMETER" if index is not None else "NOT A PARAMETER (local or absent)"
            print(f"      {type_name:34} parameter_index={index}  -> {where}")
        print()
finally:
    conn.close()

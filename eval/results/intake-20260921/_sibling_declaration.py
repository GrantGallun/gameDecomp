"""Does the sibling `src/**` file hold the struct definition the draft needs, and is it a bare type?

`renderPickupShardParticle` -> `src/race/course/race_course_props_and_pickups.c` defines
`PickupShardParticleActor`. The draft uses `arg0->transformDirty`, so it needs the BODY, and nothing in the
intake route makes a `src/**` definition available: `header_variant` scans `include/**` only.

This prints the exact declaration block, so two things can be judged before anything is wired:

  1. is it a bare type (struct/typedef/enum) or does it drag function bodies with it -- `src/**` is the
     reference decomp, and a BODY is the answer while a DECLARATION is vocabulary
  2. do its annotated offsets match the binary's accesses on that parameter

Only if both hold is making it available defensible.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import compile_obligations, workspace                          # noqa: E402

REPO = Path.home() / "decomp/sbk1"
CASES = (("renderPickupShardParticle", "PickupShardParticleActor",
          "src/race/course/race_course_props_and_pickups.c"),
         ("renderRaceCourseTripleParticle", "RaceUiTripleParticleActor",
          "src/race/ui/race_ui_effects.c"))

for function, type_name, relative in CASES:
    path = REPO / relative
    print("=" * 78)
    print(f"{function}   needs {type_name}")
    print("=" * 78)
    print(f"  {relative}: "
          f"{'present' if path.is_file() else 'MISSING'}")
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(
            r"(?:typedef\s+)?struct\s+" + re.escape(type_name) + r"\s*\{(?P<body>[^}]*)\}\s*"
            r"(?P<alias>[A-Za-z_]\w*)?\s*;", text, re.S):
        block = match.group(0)
        print(f"  declaration block ({len(block.splitlines())} lines, "
              f"{'typedef with alias ' + match.group('alias') if match.group('alias') else 'tag only'}):")
        for line in block.splitlines():
            print(f"     {line.rstrip()[:104]}")
        # Does it carry anything that is not a declaration?
        bodies = re.findall(r"\)\s*\{", block)
        print(f"  function bodies inside the block: {len(bodies)} "
              f"({'declaration only' if not bodies else 'CARRIES CODE'})")

    asm = workspace.target_asm(workspace.bootstrap(REPO, function), function)
    analysis, _ = compile_obligations.analyse(asm)
    offsets = sorted({a.address.offset for a in analysis.accesses.values()
                      if a.address and a.address.kind == "address"
                      and str(a.address.name) == "param0"})
    print(f"  binary accesses on param0 at offsets: {[hex(o) for o in offsets]}")
    annotated = {int(m.group(1), 16): m.group(2).strip()
                 for m in re.finditer(r"/\*\s*(0x[0-9A-Fa-f]+)\s*\*/\s*([^;]+);", text)}
    if annotated:
        agree = [off for off in annotated if off in offsets]
        print(f"  the file's annotated struct offsets that the binary also touches: "
              f"{[hex(o) for o in sorted(agree)]}")
        print(f"  annotated but NOT touched by this function: "
              f"{[hex(o) for o in sorted(set(annotated) - set(offsets))][:12]}")
    print()

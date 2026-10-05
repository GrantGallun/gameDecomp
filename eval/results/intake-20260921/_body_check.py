"""Does `type_bodies` find the nested-brace actor structs now, and does `recover` emit them?"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import source_type_declarations as std                        # noqa: E402

REPO = Path.home() / "decomp/sbk1"
CASES = (("src/ending/ending_credits_effects.c", "EndingCreditsEffectActor"),
         ("src/race/items/race_item_projectiles.c", "RaceItemProjectileActor"),
         ("src/menu/training/training_course_ui.c", "TrainingCourseUiActor"))

for relative, type_name in CASES:
    path = REPO / relative
    text = path.read_text(encoding="utf-8", errors="replace")
    bodies = std.type_bodies(text)
    record = bodies.get(type_name)
    print("=" * 78)
    print(f"{relative}  ->  {type_name}")
    print("=" * 78)
    print(f"  type_bodies found {len(bodies)} types in this file")
    if record is None:
        print(f"  {type_name}: NOT FOUND")
        print(f"    files types: {sorted(bodies)[:10]}")
        print()
        continue
    print(f"  FOUND; kind={record['kind']} alias={record['alias']} "
          f"annotated members={len(record['members'])}")
    print(f"  members: {sorted(record['members'])[:12]}")
    print(f"  declaration text: {len(record['text'])} chars, "
          f"{len(record['text'].splitlines())} lines")
    for line in record["text"].splitlines()[:6]:
        print(f"     {line[:96]}")
    print(f"     ...")
    print()

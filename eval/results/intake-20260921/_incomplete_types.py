"""Are the actor structs `incomplete definition` names DEFINED anywhere in the repo, or only forward-declared?

`renderRaceCourseTripleParticle` does `arg0->matrixDirty`, clang says `incomplete definition of type
'RaceUiTripleParticleActor'`, and cfe says `'matrixDirty' undefined`. Both are describing ONE fact: the type
has no visible members.

That fact has two possible causes and they have different owners:

    forward-declared, defined elsewhere   `struct T;` in one header and `struct T { ... };` in another. The
                                          definition exists, so `header_variant` can reach it -- this is a
                                          header-resolution gap, which is tooling.
    never defined                         the decomp has a name for the type and no body. Then no pass can
                                          reach it: the members' offsets are not in the binary's structure,
                                          only in the reference source, and the honest answer is to record
                                          the demand rather than to invent a layout.

This greps the repo for each name and reports which case it is. Read-only.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

REPO = Path.home() / "decomp/sbk1"
INCLUDE = REPO / "include"
SRC = REPO / "src"

NAMES = ["EndingCreditsEffectActor", "MenuPanelActor", "RacePlayerModelRenderState",
         "RaceItemProjectileActor", "TrainingCourseUiActor", "RaceItemEffectActor",
         "RaceIntroEffectActor", "RaceUiTripleParticleActor", "ControllerPakRaceRecordSaveActor",
         "PickupShardParticleActor", "EndingCreditsTumblingSnowboard", "ALParam_s"]

# A definition is `struct NAME {` or `typedef struct NAME {`; a forward declaration is `struct NAME;`.
DEFINITION = r"struct\s+{name}\s*\{{"
FORWARD = r"struct\s+{name}\s*;"

print(f"{'type':38} {'definition in':26} {'forward-declared in'}")
kinds: Counter = Counter()
for name in NAMES:
    definition_files, forward_files = [], []
    for root in (INCLUDE, SRC):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in (".h", ".c"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(DEFINITION.format(name=re.escape(name)), text):
                definition_files.append(path.relative_to(REPO).as_posix())
            elif re.search(FORWARD.format(name=re.escape(name)), text):
                forward_files.append(path.relative_to(REPO).as_posix())
    if definition_files:
        kinds["defined somewhere in the repo"] += 1
        where = definition_files[0] + (f" (+{len(definition_files) - 1})"
                                       if len(definition_files) > 1 else "")
    elif forward_files:
        kinds["forward-declared ONLY, never defined"] += 1
        where = "-- NOT DEFINED ANYWHERE --"
    else:
        kinds["neither, in headers or src"] += 1
        where = "-- NO DECLARATION AT ALL --"
    forward = forward_files[0] if forward_files else ("(none)" if not definition_files else "")
    print(f"{name:38} {where:26} {forward}")

print(f"\nverdict:")
for kind, count in kinds.most_common():
    print(f"  {count:4}  {kind}")

print("""
reading: a type that exists only as `struct T;` has no members to resolve, so `arg0->member` cannot
compile and NO deterministic pass can fix it -- the member offsets would have to be invented. A type
DEFINED in a header the candidate does not include is a header-resolution gap, and `header_variant` owns
it.""")

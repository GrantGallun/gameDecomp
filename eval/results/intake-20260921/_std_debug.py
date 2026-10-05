"""Why do three cases return a block but an empty `corroborated` map? Instrument the module's own steps."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import source_type_declarations as std                        # noqa: E402

TMP = Path("/tmp/std-debug")
(TMP / "src" / "race" / "course").mkdir(parents=True, exist_ok=True)
DECLARATION = """\
struct PickupShardParticleActor {
    char pad0[0x10];
    /* 0x10 */ u16 spawnOffsetIndex;
    char pad12[6];
    /* 0x18 */ Vec3i pos;
    /* 0x44 */ s8 transformDirty;
    char pad45[3];
    /* 0x48 */ void *image;
};

void renderPickupShardParticle(PickupShardParticleActor *arg0) {
    arg0->transformDirty = 1;
    arg0->image = 0;
}
"""
(TMP / "src" / "race" / "course" / "props.c").write_text(DECLARATION, encoding="utf-8")

SOURCE = ("void renderPickupShardParticle(PickupShardParticleActor *arg0) {\n"
          "    arg0->transformDirty = 1;\n"
          "    arg0->image = 0;\n"
          "}\n")

print("1. target_source_file:")
path = std.target_source_file(TMP, "build/src/race/course/props.o")
print(f"   {path}")

print("\n2. type_bodies:")
bodies = std.type_bodies(DECLARATION)
for name, record in bodies.items():
    print(f"   {name}: tag={record['tag']!r} alias={record['alias']!r} members={record['members']}")

print("\n3. referenced_types(source, 'renderPickupShardParticle'):")
print(f"   {std.referenced_types(SOURCE, 'renderPickupShardParticle')}")

print("\n4. the parameter lookup in recover():")
import re                                                                 # noqa: E402
name = "PickupShardParticleActor"
matches = list(re.finditer(r"[A-Za-z_][\w \t*]*\b" + re.escape(name) + r"\s*\*+\s*(?P<var>[A-Za-z_]\w*)",
                           SOURCE))
print(f"   matches: {[m.group(0) for m in matches]}")
for index, match in enumerate(matches):
    print(f"   index {index} -> would be param{index} (var={match.group('var')})")
print("   NOTE: `enumerate` over matches is the parameter INDEX only when the type name appears once, "
      "per reference, in parameter order. Check that here.")

print("\n5. members `used`, as recover() computes them:")
used = sorted({m.group(1) for m in
               re.finditer(r"\b\w+\s*->\s*(?P<member>[A-Za-z_]\w*)", SOURCE)})
print(f"   {used}")
member_set = set(bodies[name]["members"])
print(f"   in the declaration: {sorted(member_set)}")

print("\n6. the call:")
block, receipt = std.recover(SOURCE, function="renderPickupShardParticle", repo=TMP,
                             target="build/src/race/course/props.o", assembly="",
                             binary_offsets={"param0": {0x44, 0x48}})
print(f"   block: {bool(block)}")
print(f"   recovered: {receipt['recovered']}")
print(f"   corroborated: {receipt['corroborated']}")
print(f"   on_project_authority: {receipt['on_project_authority']}")
print(f"   declined: {receipt['declined']}")

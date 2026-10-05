"""Call `typedecl.plan` directly with a known-good layout, so each filter is isolated.

Two in-process experiments have now failed to move `opaque_variant`, and both times the reasoning was "this
filter must be the one". That is the same mistake in miniature, so this stops reasoning and calls `plan`
with a MINIMAL input where the expected answer is obvious:

    source   s32 f(void *arg0) { return arg0->unkC; }
    layout   {"param0": [(12, 4, "s32")]}

If `plan` returns a struct for that, the filters are fine and the blocker is upstream (the layout the
caller builds). If it returns nothing, the next line printed says which `continue` fired.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import structgen, typedecl                                     # noqa: E402

SOURCE = "s32 f(void *arg0) { return arg0->unkC; }\n"
LAYOUT = {"param0": [(12, 4, "s32")]}

print(f"source: {SOURCE.strip()}")
print(f"layout: {LAYOUT}")

print("\nstep by step through plan()'s filters:")

params = typedecl.definition_params(SOURCE, "f")
print(f"  definition_params      -> {params}")

pointers = typedecl.pointer_parameters(SOURCE, "f")
print(f"  pointer_parameters     -> {pointers}")

primitives = typedecl.PRIMITIVE_TYPES
print(f"  PRIMITIVE_TYPES has void -> {'void' in primitives}")

for index, type_name, var in pointers:
    in_known = False
    in_primitives = type_name in primitives
    in_declared = typedecl.declared_in(SOURCE, type_name)
    print(f"  param {index} type={type_name!r} var={var!r}: "
          f"known={in_known} primitive={in_primitives} declared_in_source={in_declared} "
          f"-> {'SKIPPED' if (in_primitives or in_declared) else 'kept'}")

print(f"\n  members_used(source, vars) -> {typedecl.members_used(SOURCE, {'arg0'})}")

plans = typedecl.plan(SOURCE, "f", LAYOUT, set())
print(f"\nplan(source, 'f', LAYOUT, set()) -> {len(plans)} plan(s)")
for plan in plans:
    print(f"  type={plan['type']!r} params={plan['params']} offsets={plan['offsets']} "
          f"named={plan['named']} source={plan['source']}")
    print(f"  text: {plan['text']}")

# And with void demoted, to see whether that filter was load-bearing at all.
original = typedecl.PRIMITIVE_TYPES
try:
    typedecl.PRIMITIVE_TYPES = frozenset(original - {"void"})
    plans = typedecl.plan(SOURCE, "f", LAYOUT, set())
    print(f"\nwith void removed from PRIMITIVE_TYPES -> {len(plans)} plan(s)")
    for plan in plans:
        print(f"  type={plan['type']!r} named={plan['named']}")
        print(f"  text: {plan['text']}")
finally:
    typedecl.PRIMITIVE_TYPES = original

print(f"\nstructgen.render('T', [(12,4,'s32')], {{12: 'unkC'}}):")
print("  " + structgen.render("T", [(12, 4, "s32")], {12: "unkC"}).replace("\n", "\n  "))

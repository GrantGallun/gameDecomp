"""Why is `f` not added when `PR/f.h` declares it? Ask the real functions, not a reimplementation.

The trace showed the sibling (`__State` -> `PRinternal/state.h`) being added and `f` resolving to `PR/f.h`
in my reimplementation of the choice logic, so the difference must be in what the REAL code computes. This
calls those functions directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import project_headers                                        # noqa: E402

TMP = Path("/tmp/header-variant-trace")
print("include root exists:", (TMP / "include").is_dir())
print("PR/f.h exists       :", (TMP / "include" / "PR" / "f.h").is_file())

found = project_headers.declarations(TMP, "f")
print(f"\ndeclarations(TMP, 'f') -> {len(found)}")
for declaration in found:
    print(f"   {declaration}")

print("\nthe SDK filter in header_variant is:")
print("   [d.include for d in declarations(...) if not d.include.startswith('game/')]")
print(f"   -> {[d.include for d in found if not d.include.startswith('game/')]}")

called = project_headers.called_functions("glabel f")
print(f"\ncalled_functions('glabel f') -> {called}")

# And the full variant, with the report, to see what it decided.
from solver import compile_recovery                                       # noqa: E402
source = '#include "common.h"\n#include "game/shim.h"\nvoid f(void) { __State *s = __states; }'
changed, report = compile_recovery.header_variant(TMP, "f", "glabel f", source, "build/src/ultra/f.o")
print(f"\nreport['added'] -> {report['added']}")
print(f"retained after  -> {[line for line in changed.splitlines() if line.startswith('#include')]}")

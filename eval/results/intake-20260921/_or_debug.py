"""Look at what `m2c_or_address.rewrite` actually does to one line, and why the write form loses a character."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import m2c_or_address as oa                                   # noqa: E402

CASES = [
    "    *arg2 = *(arg0->unkC | arg1 | (s32) &D_A0000000);\n",
    "    *(arg0->unkC | arg1) = arg2;\n",
    "void f(void) {\n    *p = *(a->b | c);\n}\n",
    "void f(void) {\n    *p = *(q);\n}\n",
]
for source in CASES:
    out, changes = oa.rewrite(source)
    print(f"in : {source!r}")
    print(f"out: {out!r}")
    print(f"changes: {changes}")
    # what `_matching_close` finds
    star = source.find("*(")
    if star >= 0:
        close = oa._matching_close(source, star + 1)
        print(f"  first '*( ' at {star}, matching close {close}, chain={source[star + 2:close]!r}")
    print()

"""What does `--valid-syntax` actually change, and why does the arm convert FEWER states?

Two candidate explanations and only the text separates them:

  (a) the flag does not remove the constructs the residual is made of (`unk-4`, `(bitwise f32)`), so the
      hypothesis that the remaining 31 are "a flag" is simply wrong;
  (b) the arm lost something the default draft had. `workspace.m2c_draft` returns
      `'#include "common.h"\\n\\n' + stdout`, and this script prepends the same string -- so if the
      difference is not the include, it is in what m2c emits when told to be syntax-valid.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import m2c_input, workspace                                  # noqa: E402

REPO = Path.home() / "decomp/sbk1"
NAMES = ("alSavePull", "memcpy", "osSyncPrintf", "alSynSetPan")

for name in NAMES:
    ws = workspace.bootstrap(REPO, name)
    default = workspace.m2c_draft(ws)
    result, _meta = m2c_input.draft(REPO, ws / "target.s", valid_syntax=True)
    valid = ('#include "common.h"\n\n' + result.stdout) if result.returncode == 0 else "<m2c failed>"
    print("=" * 78)
    print(f"{name}   default {len(default)} chars   valid_syntax {len(valid)} chars")
    print("=" * 78)
    print("--- default (workspace.m2c_draft) ---")
    for line in default.splitlines()[:16]:
        print(f"   {line[:110]}")
    print("--- valid_syntax ---")
    for line in valid.splitlines()[:16]:
        print(f"   {line[:110]}")
    same = default.strip() == valid.strip()
    print(f"   identical: {same}")
    print()

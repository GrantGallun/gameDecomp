"""Why is the checker recipe unavailable, and what SHOULD the target be?

`frontend_check.recipe(repo, makefile_text, target)` projects the project Makefile for one TU and rejects
anything that is not `build/src/...o` ("unsupported TU object identity"). The pilot called it with an empty
target, which cannot work -- but the same rejection appeared by hand earlier for states whose recipe target
looked correct, so the real question is what the recipe resolver expects and what the context carries.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.tool_agent_run import build_context                             # noqa: E402
from solver import compiler_recipe, frontend_check                        # noqa: E402

KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

conn = sqlite3.connect(str(KB))
try:
    for name in ("initMenuAssetHandles", "alSavePull"):
        context, why = build_context(REPO, name, conn=conn)
        print("=" * 78)
        print(name)
        print("=" * 78)
        if context is None:
            print(f"  build_context: {why}")
            continue
        print(f"  context.target            : {context.target!r}")
        recipe = (context.initial_verdict or {}).get("compiler_recipe") or {}
        print(f"  compiler_recipe['target'] : {recipe.get('target')!r}")
        ws = Path(context.workspace) if context.workspace else REPO / "nonmatchings" / name
        identity = ws / ".compiler-target.json"
        print(f"  .compiler-target.json     : "
              f"{json.loads(identity.read_text())['target'] if identity.is_file() else '(missing)'}")
        # What does the resolver itself say?
        target = recipe.get("target") or ""
        try:
            resolved = compiler_recipe.resolve(REPO, target)
            print(f"  compiler_recipe.resolve   : ok, keys={sorted(resolved)}")
            print(f"     resolved target        : {resolved.get('target')!r}")
        except Exception as exc:                                          # noqa: BLE001
            print(f"  compiler_recipe.resolve   : {type(exc).__name__}: {exc}")
        # And the frontend recipe, with the target the recipe actually recorded.
        try:
            selected = frontend_check.recipe(str(REPO), (REPO / "Makefile").read_text(), target)
            print(f"  frontend_check.recipe     : ok, CC_CHECK={selected['settings'].get('CC_CHECK')!r}")
            print(f"     command                : {' '.join(selected['command'])[:150]}")
        except Exception as exc:                                          # noqa: BLE001
            print(f"  frontend_check.recipe     : {type(exc).__name__}: {exc}")
        print()
finally:
    conn.close()

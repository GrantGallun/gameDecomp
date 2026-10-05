"""Why does `header_variant` leave 32 header-declared globals undeclared -- `gRenderMatricesDirty` in 23 states?

The name is declared in a project header, so by the mechanism's own rules it should be pulled in. Three
candidate explanations and the first two are checkable without compiling anything:

  1. the name never reaches the search   -- `used` is built from `X *`, `g[A-Z]\\w*`/`__\\w+`, and function
                                            names; a global used as a VALUE (`gX = 1;`) needs the `g[A-Z]`
                                            arm, so this should be fine unless it is provided/known already
  2. a header declares it but the search rejects that header (SDK filter, or the declaration is inside a
     macro/conditional the scan does not read)
  3. the header IS added and the identifier is still undeclared -- which would mean the declaration is
     conditional on something the candidate does not define, and needs `-D` rather than an include

This checks 1 and 2 directly for the top names, then runs the real `header_variant` on the real draft of
the state that `gRenderMatricesDirty` blocks most.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import project_headers                                        # noqa: E402

REPO = Path.home() / "decomp/sbk1"
INCLUDE = REPO / "include"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

split = json.loads((BASE / "undeclared-class-split.json").read_text(encoding="utf-8"))
header_names = [name for name, info in split["names"].items() if info["in_headers"]]
print(f"names declared in a header, by how many blocked states they hold back:")
for name in sorted(header_names, key=lambda n: -split["names"][n]["states"])[:14]:
    print(f"  {split['names'][name]['states']:4}  {name}")

print("\nwhere each is declared, and whether the header's path is SDK-visible:")
for name in sorted(header_names, key=lambda n: -split["names"][n]["states"])[:8]:
    hits = []
    for header in INCLUDE.rglob("*.h"):
        text = header.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"\b" + re.escape(name) + r"\b", text):
            continue
        relative = header.relative_to(INCLUDE).as_posix()
        declaration = re.search(r"(?m)^[^\n]*\b" + re.escape(name) + r"\b[^\n]*", text)
        hits.append((relative, (declaration.group(0).strip()[:70] if declaration else "")))
    print(f"\n  {name}  ({split['names'][name]['states']} states)")
    for relative, line in hits[:4]:
        sdk_hidden = relative.startswith("game/")
        print(f"     {'[game/ - SDK filter drops it] ' if sdk_hidden else '[visible] '}"
              f"{relative:52} {line}")
    if not hits:
        print("     (no header contains the token at all)")

# Which state does gRenderMatricesDirty block, and does header_variant add its header?
TARGET = "gRenderMatricesDirty"
blocked_by = [row for row in split["rows"] if TARGET in row["names"]]
print(f"\nstates blocked by {TARGET}: {len(blocked_by)}; first is {blocked_by[0]['function']!r}"
      if blocked_by else f"\nno state is blocked by {TARGET}")

if blocked_by:
    name = blocked_by[0]["function"]
    import sqlite3
    from eval.tool_agent_run import build_context
    from eval.intake_runners import header_variant

    conn = sqlite3.connect(str(Path.home() / "decomp/kb-sbk1.sqlite"))
    try:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"  build_context: {why}")
        else:
            result = header_variant({**context.__dict__, "kb_conn": conn}, {})
            detail = result.get("detail") or {}
            added = [a for a in (detail.get("added") or [])]
            print(f"  header_variant on {name}: changed={result.get('changed')} "
                  f"added={len(added)}")
            for entry in added:
                print(f"     added {entry.get('identifier')} <- {entry.get('header')}")
            print(f"  {TARGET} among the additions: "
                  f"{any(a.get('identifier') == TARGET for a in added)}")
    finally:
        conn.close()

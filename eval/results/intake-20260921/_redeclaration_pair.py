"""The exact pair cfe calls a redeclaration: what the draft defines, and what the added header declares.

`header_variant` adds a header for a NAME it found in the draft. For the function's own name that is
always wrong -- the draft defines it, and the header prototypes it -- so the question is only which of the
four are that case and which are a struct/typedef conflict.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402
from solver import project_headers                                       # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

classes = json.loads((BASE / "post-fix-residual-classes.json").read_text(encoding="utf-8"))
TARGETS = classes["classes"]["redeclaration against an injected header"]

conn = sqlite3.connect(str(KB))
try:
    for name in TARGETS:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            continue
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception:                                        # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        line = next((ln for ln in stderr.splitlines() if "redeclaration" in ln), "")
        match = re.search(r"redeclaration of '(?P<name>\w+)'", line)
        symbol = match.group("name") if match else "?"
        print("=" * 78)
        print(f"{name}")
        print("=" * 78)
        print(f"  cfe: {line.strip()[:120]}")
        print(f"  symbol redeclared: {symbol}")
        provided = project_headers._included_declarations(REPO, current)
        print(f"  what the included headers provide for it:")
        for decl in sorted(set(provided.get(symbol, [])))[:3]:
            print(f"     {' '.join(decl.split())[:110]}")
        if symbol not in provided:
            print("     (nothing -- the conflict is not a declaration the helper can see)")
        print(f"  what the CANDIDATE says, top level:")
        for start, end, decl in project_headers._top_level_declarations(current):
            if symbol in decl:
                print(f"     {' '.join(decl.split())[:110]}")
        for index, text in enumerate(current.splitlines(), 1):
            if re.match(rf"^\s*(?:struct|typedef).*\b{re.escape(symbol)}\b", text) or \
                    re.match(rf"^[A-Za-z_][\w \t*]*\b{re.escape(symbol)}\s*\(", text):
                print(f"     line {index}: {text.strip()[:100]}")
        print()
finally:
    conn.close()

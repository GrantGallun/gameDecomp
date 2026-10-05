"""Is an undeclared type name a missing include, or a type nobody ever defined?

`header_variant` adds headers by looking up identifiers it recognises. If `CourseGridEntry` is declared in
some header under `include/` and the candidate simply does not include it, then adding that include is
exactly the mechanism that already exists -- and the reason it did not fire is that the type name appears
in a declaration position rather than as a called identifier, which is not what `header_variant` searches
for.

This greps `include/` for each undeclared type name, then compiles the candidate with that header added.
The object decides. Read-only: nothing is written to the KB or promoted.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                      # noqa: E402
from eval.intake_runners import RUNNERS                                  # noqa: E402
from eval.tool_agent_run import build_context                            # noqa: E402

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
INCLUDE = REPO / "include"

# Names that are statement keywords, not types: the declaration regex mis-reads them. Excluded so the
# count is of TYPE names and not of parser artefacts of this script.
NOT_A_TYPE = {"return", "goto", "break", "continue", "if", "else", "while", "for", "switch", "case",
              "do", "sizeof", "typedef", "extern", "static", "signed", "unsigned"}
findings = json.loads((BASE / "post-fix-undefined-types.json").read_text(encoding="utf-8"))["rows"]
targets = []
for row in findings:
    name = row["first_undefined_type"]
    if not name or name in NOT_A_TYPE:
        continue
    targets.append((row["function"], name))

# Where is each type declared? One grep over the project's headers.
index: dict[str, list[str]] = {}
for header in INCLUDE.rglob("*.h"):
    text = header.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r"\btypedef\s+(?:struct|union|enum)?\s*[\w\s]*?\b(?P<name>[A-Za-z_]\w*)\s*;",
                             text):
        index.setdefault(match.group("name"), []).append(str(header.relative_to(INCLUDE)))
    for match in re.finditer(r"\b(?:struct|union|enum)\s+(?P<name>[A-Za-z_]\w*)\s*\{", text):
        index.setdefault(match.group("name"), []).append(str(header.relative_to(INCLUDE)))

print(f"{len(targets)} blocked states name a type that is not declared in the candidate\n")
found = converted = 0
conn = sqlite3.connect(str(KB))
try:
    for name, type_name in targets:
        headers = sorted(set(index.get(type_name, [])))
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
        if best.get("compiled"):
            continue
        if not headers:
            print(f"  {name:40} {type_name:26} NO HEADER DECLARES IT")
            continue
        found += 1
        added = "".join(f'#include "{h}"\n' for h in headers[:3])
        source = added + current
        verdict = context.compile_fn(source)
        status = ("compiled" if verdict.get("compiled")
                  else ("exact" if verdict.get("exact") else "still fails"))
        converted += int(bool(verdict.get("compiled")))
        first = next((ln for ln in (verdict.get("stderr") or "").splitlines() if ln.strip()), "")
        print(f"  {name:40} {type_name:26} +{headers[0][:34]:34} -> {status:11} {first[:40]}")
finally:
    conn.close()

print(f"\n{found} of {len(targets)} had a header declaring the type; adding it compiled {converted}")

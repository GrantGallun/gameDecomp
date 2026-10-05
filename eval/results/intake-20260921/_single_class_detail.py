"""The actual names and messages blocking the 58 single-class states.

`_gap_analysis.py` reported "0 names a header declares, 0 names no header declares" for the 27
undeclared-identifier states, which cannot both be true unless the scan found no names at all -- it read
`row["actions"][frontend_diagnostics]["detail"]["errors"]`, and the trace payload's action detail is taken at
ONE step, not at the end. Same point-in-the-pipeline trap as everything else this session, so the
diagnostics are re-read on the FINAL candidate here.

Read-only.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import frontend_diagnostics as frontend                        # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

payload = json.loads((BASE / "wide-intake-orfix.json").read_text(encoding="utf-8"))
targets: list[tuple[str, str]] = []
for row in payload["rows"]:
    if row["sequence"]["compiled"]:
        continue
    classes = (row.get("diagnostic_trace") or [{}])[-1].get("classes") or []
    if len(classes) == 1:
        targets.append((row["function"], classes[0]))

print(f"single-class states: {len(targets)}")

# What every project header declares, however it is spelled.
declared: set[str] = set()
for header in (REPO / "include").rglob("*.h"):
    text = header.read_text(encoding="utf-8", errors="replace")
    declared.update(re.findall(r"\bextern\s+[A-Za-z_][\w \t*]*?([A-Za-z_]\w*)\s*[\[;(]", text))
    declared.update(re.findall(r"(?m)^\s*[A-Za-z_][\w \t*]*?\b([A-Za-z_]\w*)\s*\(", text))
    declared.update(re.findall(r"\b([A-Za-z_]\w*)\s*[\[;]", text))
print(f"identifiers appearing in include/**: {len(declared):,}\n")

UNDECLARED = re.compile(r"use of undeclared identifier '([A-Za-z_]\w*)'|"
                        r"undeclared identifier '([A-Za-z_]\w*)'")
STACK = re.compile(r"^sp[0-9A-Fa-f]*$")

by_class: dict[str, list[tuple[str, list[str], str]]] = defaultdict(list)
conn = sqlite3.connect(str(KB))
try:
    for name, kind in targets:
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
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        report = frontend.analyse(current, repo=REPO, target=str(context.target or ""))
        messages = [e["what"] for e in (report.get("errors") or [])]
        names = []
        for message in messages:
            match = UNDECLARED.search(message)
            if match:
                found = match.group(1) or match.group(2)
                if found not in names:
                    names.append(found)
        by_class[kind].append((name, names, messages[0][:70] if messages else ""))
finally:
    conn.close()

for kind in ("undeclared-identifier", "member-on-typed-pointer", "unclassified",
             "undeclared-member", "other-syntax", "redeclaration/conflict",
             "incompatible-pointer"):
    states = by_class.get(kind) or []
    if not states:
        continue
    print("=" * 78)
    print(f"{kind}   ({len(states)} single-class states)")
    print("=" * 78)
    all_names: Counter = Counter()
    for name, names, first in states:
        for found in names:
            all_names[found] += 1
    if all_names:
        in_headers = [n for n in all_names if n in declared and not STACK.match(n)]
        stacks = [n for n in all_names if STACK.match(n)]
        absent = [n for n in all_names if n not in declared and not STACK.match(n)]
        print(f"  names: {len(all_names)} distinct")
        print(f"    declared in a header : {len(in_headers)}  {sorted(in_headers)[:6]}")
        print(f"    stack slots (spNN)   : {len(stacks)}  {sorted(stacks)[:6]}")
        print(f"    in NO header         : {len(absent)}  {sorted(absent)[:8]}")
    print(f"  the states and their first message:")
    for name, _names, first in states[:12]:
        print(f"    {name:42} {first}")
    print()

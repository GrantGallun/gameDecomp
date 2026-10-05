"""Do the src-side struct definitions' offsets AGREE with the binary's observed accesses?

This is the question that decides whether the largest class is reachable, and it has a clean answer that is
not a matter of opinion: the project's own source files carry the struct bodies with offsets in comments
(`/* 0x44 */ s8 transformDirty;`), and the target assembly carries the accesses. If the offsets agree for
the members the draft touches, the definition is evidence the binary corroborates -- not an invented
layout.

WHY THIS IS NOT CONTAMINATION, stated where it matters. `src/**` is the reference decomp, and CLAUDE.md's
rule is that reference source is for checking, never for feeding the solver. The rule's purpose is that a
copied BODY answers the question. A struct DECLARATION is shared vocabulary: `include/**` already supplies
it, `header_variant` already reads it, and the tier list already calls those matches `header-assisted`
rather than `SOLVED`. So the distinction to test is declaration-versus-body, and the corroboration test is
what keeps the declaration honest: an offset that the binary contradicts is a guess, and is reported as one.

READ-ONLY: it compares two sources of evidence and prints the agreement. Nothing is wired.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, workspace                          # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

# `/* 0x44 */ s8 transformDirty;` -- the project's own offset annotations.
FIELD = re.compile(r"/\*\s*(?P<off>0x[0-9A-Fa-f]+)\s*\*/\s*(?P<decl>[^;]+);")


def source_structs() -> dict[str, dict[str, int]]:
    """{type: {member: offset}} from every offset-annotated struct body under src/."""
    found: dict[str, dict[str, int]] = {}
    for path in (REPO / "src").rglob("*.c"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(
                r"(?:typedef\s+)?struct\s+(?P<name>[A-Za-z_]\w*)\s*\{(?P<body>[^}]*)\}\s*"
                r"(?P<alias>[A-Za-z_]\w*)?\s*;", text, re.S):
            members = {}
            for field in FIELD.finditer(match.group("body")):
                decl = field.group("decl").strip()
                name = re.findall(r"[A-Za-z_]\w*", decl)
                if not name:
                    continue
                members[name[-1]] = int(field.group("off"), 16)
                members[f"__decl__{name[-1]}"] = decl
            if members:
                found.setdefault(match.group("name"), {}).update(members)
                if match.group("alias"):
                    found.setdefault(match.group("alias"), {}).update(members)
    return found


structs = source_structs()
print(f"offset-annotated struct bodies found under src/: {len(structs)}\n")

intake = json.loads((BASE / "wide-intake-traced.json").read_text(encoding="utf-8"))
blocked = [row for row in intake["rows"] if not row["sequence"]["compiled"]]

conn = sqlite3.connect(str(KB))
verdicts: Counter = Counter()
checked = 0
try:
    for row in blocked[:14]:
        function = row["function"]
        context, why = build_context(REPO, function, conn=conn)
        if context is None:
            continue
        header = next((ln for ln in (context.candidate or "").splitlines()
                       if function + "(" in ln), "")
        type_match = re.search(r"\b([A-Za-z_]\w*)\s*\*\s*arg0", header)
        if not type_match:
            continue
        type_name = type_match.group(1)
        members = structs.get(type_name) or {}
        if not members:
            continue
        # The binary's accesses on param0.
        asm = workspace.target_asm(workspace.bootstrap(REPO, function), function)
        analysis, _ = compile_obligations.analyse(asm)
        offsets = {a.address.offset for a in analysis.accesses.values()
                   if a.address and a.address.kind == "address"
                   and str(a.address.name) == "param0"}
        used = sorted({n for n in re.findall(r"arg0\s*->\s*([A-Za-z_]\w*)", context.candidate or "")})
        agree = [m for m in used if isinstance(members.get(m), int) and members[m] in offsets]
        disagree = [m for m in used if isinstance(members.get(m), int) and members[m] not in offsets]
        unknown = [m for m in used if m not in members]
        checked += 1
        if used and not disagree and agree:
            verdicts["corroborated"] += 1
        elif disagree:
            verdicts["contradicted"] += 1
        else:
            verdicts["no overlap to check"] += 1
        print(f"{function}  ({type_name})")
        print(f"   binary offsets on param0 : {sorted(offsets)}")
        print(f"   members the draft uses   : {used}")
        print(f"   source offset AGREES     : "
              f"{[(m, hex(members[m])) for m in agree]}")
        if disagree:
            print(f"   source offset DISAGREES  : "
                  f"{[(m, hex(members[m])) for m in disagree]}")
        if unknown:
            print(f"   not in the source struct : {unknown}")
        print()
finally:
    conn.close()

print(f"checked {checked} states:")
for kind, count in verdicts.most_common():
    print(f"  {count:4}  {kind}")

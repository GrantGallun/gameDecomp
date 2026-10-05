"""The residual after every repair, classified by WHAT THE DRAFT'S OWN TEXT IS.

`post-fix-residual.json` gives the first compiler error and the offending line for each still-blocked
state. This groups them by the construct in that line rather than by the compiler's message, because
`Syntax Error` is a symptom and the construct is the thing a fix has to be written against.

The classes are mutually exclusive in the order they are tested, and a line that matches nothing is
reported as unclassified rather than folded into the nearest bucket.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
payload = json.loads((BASE / "post-fix-residual.json").read_text(encoding="utf-8"))
blocked = [r for r in payload["rows"] if not r["compiled"]]

RULES = (
    ("negative field offset (`->unk-N`, not an identifier)",
     re.compile(r"->\s*\w*unk-\w+|\.\s*\w*unk-\w+|unk-\w+\s*=")),
    ("pseudo-cast `(bitwise T)`",
     re.compile(r"\(\s*bitwise\s")),
    ("pseudo-cast `(unaligned T)`",
     re.compile(r"\(\s*unaligned\s")),
    ("`M2C_FIELD` macro form",
     re.compile(r"\bM2C_FIELD\b")),
    ("`?` inside a struct body",
     re.compile(r"^\s*/\*[^*]*\*/\s*\?\s")),
    ("redeclaration against an injected header",
     re.compile(r"^redeclaration of", re.I)),
    ("undeclared member (`->name` on an untyped pointer)",
     re.compile(r"^\s*'?\w+'? undefined|undefined; reoccurrences")),
    ("illegal character in a numeric literal",
     re.compile(r"Illegal character", re.I)),
)

groups: dict[str, list[dict]] = defaultdict(list)
for row in blocked:
    text = row.get("source_line") or ""
    message = row.get("first_error") or ""
    for label, pattern in RULES:
        if pattern.search(text) or pattern.search(message):
            groups[label].append(row)
            break
    else:
        groups["unclassified"].append(row)

print(f"{len(blocked)} still-blocked states, by the construct in the draft's own text:")
for label, entries in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    print(f"\n  {len(entries):3}  {label}")
    for row in entries[:6]:
        print(f"        {row['function']:40} line {str(row.get('line')):>4}: "
              f"{(row.get('source_line') or row.get('first_error') or '')[:74]}")
    if len(entries) > 6:
        print(f"        ... {len(entries) - 6} more")

print()
print("what is ALREADY BUILT for each class, and whether the intake route calls it:")
PRESENT = {
    "negative field offset (`->unk-N`, not an identifier)":
        "nothing found: no module rewrites `unk-N`",
    "pseudo-cast `(bitwise T)`":
        "solver/m2c_context.py lowers `(bitwise s32|u32|f32)` (line 130); NOT in the intake sequence",
    "pseudo-cast `(unaligned T)`":
        "solver/m2c_byte_view.py handles M2C_UNALIGNED32 reads/stores; reached from compile_recovery, "
        "NOT from the intake sequence",
    "`M2C_FIELD` macro form":
        "produced by `--valid-syntax`; `solver/compile_fix_prompt.py` names it as not-C. Measured: the "
        "flag converts 1 of 40, WORSE than the default draft, so this form is not a route",
    "`?` inside a struct body":
        "solver/m2c_placeholders.py now finds these (the line-start rule); rewrite() typed 4 of the "
        "drafts carrying one. The rest remain, see initCourseDetailsMenu",
    "redeclaration against an injected header":
        "self-inflicted by `header_variant` adding an include that defines a symbol the draft also "
        "declares; nothing dedupes the two",
    "undeclared member (`->name` on an untyped pointer)":
        "`opaque-struct` (solver/compile_obligations.opaque_variant) is the owner; declined 40 of 40 on "
        "this frame without ever reaching one of these errors",
    "illegal character in a numeric literal":
        "not investigated; one state",
}
for label in sorted(groups, key=lambda k: -len(groups[k])):
    print(f"  {len(groups[label]):3}  {label}")
    print(f"       {PRESENT.get(label, 'not investigated')}")

(BASE / "post-fix-residual-classes.json").write_text(json.dumps(
    {"classes": {label: [r["function"] for r in entries] for label, entries in groups.items()},
     "already_built": PRESENT,
     "note": "classified from the draft's own text, not from the compiler's message"}, indent=2) + "\n",
    encoding="utf-8")
print(f"\nwrote {BASE / 'post-fix-residual-classes.json'}")

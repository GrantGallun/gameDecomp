"""Declare identifiers IDO reports as undefined, from the candidate's own uses (2026-09-15 non-compiling census).

Of 78 pending functions whose source does not compile, the largest first-error class was
`'X' undefined` (26, plus 2 more once placeholder recovery fixed their first line): m2c drafts that
use a stack slot or a global they never declared. IDO reports every such name at once. The names
seen: 103 named globals or members, 45 `spNN` stack slots, 14 `D_XXXXXXXX` globals.

Only compiler-reported names are declared, and only from how this candidate uses them. No
reference source is read. Each declaration is the least committal one that compiles; the byte
residual, not this module, decides whether the width is right:

    spNN used by address or index   u8 spNN[size]   size = gap to the next named stack slot (else 4)
    spNN used as a value             s32 spNN;
    global used only by address      extern u8 NAME;          (byte-unit arithmetic, as m2c wrote it)
    global indexed                   extern s32 NAME[];
    global used as a value           extern s32 NAME;

Declines: a bare `unkNN` (a struct member m2c flattened: no declaration is honest), and any name
used with `.` or `->` (its aggregate type is unknown). Every decline is reported.
"""
from __future__ import annotations

import re

from solver import repair_context

UNDEFINED = re.compile(r"'([A-Za-z_]\w*)' undefined|undeclared identifier '([A-Za-z_]\w*)'")
STACK_SLOT = re.compile(r"^sp([0-9A-Fa-f]+)$")
MEMBER_LIKE = re.compile(r"^unk[0-9A-Fa-f]+$")


def undefined_names(*diagnostics: str) -> list[str]:
    names = []
    for text in diagnostics:
        for a, b in UNDEFINED.findall(text or ""):
            name = a or b
            if name not in names:
                names.append(name)
    return names


def _uses(body: str, name: str) -> dict:
    """Classify every occurrence: member (`n.` / `n->`), index (`n[`), address (`&n`), else value."""
    use = {"any": False, "member": False, "address": False, "index": False, "value": False}
    for found in re.finditer(rf"(?<![\w.>])\b{re.escape(name)}\b", body):
        use["any"] = True
        before = body[:found.start()].rstrip()
        after = body[found.end():].lstrip()
        if after.startswith((".", "->")):
            use["member"] = True
        elif after.startswith("["):
            use["index"] = True
        elif before.endswith("&") and not before.endswith("&&"):
            use["address"] = True
        else:
            use["value"] = True
    return use


def _slot_sizes(body: str, names: list[str]) -> dict[str, int]:
    offsets = sorted({int(m.group(1), 16) for m in re.finditer(r"\bsp([0-9A-Fa-f]+)\b", body)})
    sizes = {}
    for name in names:
        slot = STACK_SLOT.match(name)
        if not slot:
            continue
        offset = int(slot.group(1), 16)
        higher = [o for o in offsets if o > offset]
        sizes[name] = min(higher[0] - offset, 0x100) if higher else 4
    return sizes


def propose(source: str, function: str, diagnostics: str) -> tuple[list[tuple[str, str]], dict]:
    """[(label, candidate)] declaring the reported undefined names, plus a report of each decision."""
    report = {"declared": [], "declines": []}
    names = undefined_names(diagnostics)
    if not names:
        return [], report
    try:
        match, end = repair_context.definition(source, function)
    except ValueError as exc:
        report["declines"].append({"reason": str(exc)})
        return [], report
    body = source[match.end():end]
    signature = source[match.start():source.index("{", match.start(), end)]
    sizes = _slot_sizes(body, names)
    locals_, externs = [], []
    for name in names:
        use = _uses(body, name)
        if MEMBER_LIKE.match(name):
            report["declines"].append({"name": name, "reason": "bare unkNN is a flattened struct member"})
            continue
        if re.search(rf"(?:\.|->)\s*{re.escape(name)}\b", source):
            # IDO also says `'data' undefined` for a member its struct lacks (`spC.data[0xA]` in
            # __osContGetInitData, where `data` is also the parameter); a declaration cannot fix that.
            report["declines"].append({"name": name, "reason": "used as a member name; the struct lacks it"})
            continue
        if re.search(rf"\b{re.escape(name)}\s*[,)]", signature):
            report["declines"].append({"name": name, "reason": "already a parameter"})
            continue
        if not use["any"]:
            report["declines"].append({"name": name, "reason": "not used in the function body"})
            continue
        if use["member"]:
            report["declines"].append({"name": name, "reason": "used as an aggregate; its type is unknown"})
            continue
        if STACK_SLOT.match(name):
            if use["address"] or use["index"]:
                declaration = f"u8 {name}[{sizes.get(name, 4)}];"
            else:
                declaration = f"s32 {name};"
            locals_.append(declaration)
        elif use["index"]:
            externs.append(f"extern s32 {name}[];")
            declaration = externs[-1]
        elif use["address"] and not use["value"]:
            externs.append(f"extern u8 {name};")
            declaration = externs[-1]
        else:
            externs.append(f"extern s32 {name};")
            declaration = externs[-1]
        report["declared"].append({"name": name, "declaration": declaration})
    if not locals_ and not externs:
        return [], report
    brace = source.index("{", match.start(), end) + 1
    text = source
    if locals_:                                   # declarations first in the body, as C89/IDO requires
        text = text[:brace] + "\n" + "".join(f"    {d}" + "\n" for d in locals_) + text[brace:].lstrip("\n")
    if externs:
        text = text[:match.start()] + "".join(f"{d}\n" for d in externs) + text[match.start():]
    return [("undeclared:declare", text)], report

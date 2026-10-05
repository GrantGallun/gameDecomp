"""Unused file-scope object definitions: a fault the text diff cannot show.

A candidate that defines an object at file scope (`float sp18[4][4];`, `int padding[2];`) gets a .bss or .data
section the target object does not have. The function's instructions are unaffected, so the normalized diff is empty
or unchanged and every diff-driven repair declines ("no mismatching instruction mapped to a source line"). The names
(`dummy`, `padding`, `spNN`) suggest stack-padding edits written outside the function, where they cannot change the
frame.

Measured 2026-09-30 (eval/results/hidden-object-20260930): 20 of 828 unsolved best candidates carry an extra .bss;
guMtxIdent's only fault was `float sp18[4][4];`, and deleting it made the object exact. This deletes only definitions
no other line of the source mentions, and only when the target object has no .bss/.data of its own. The compiler
decides whether the result is better.
"""
from __future__ import annotations

import re
from pathlib import Path

_DEFINITION = re.compile(
    r"^(?!\s*(?:extern|typedef|return|goto)\b)[ \t]*(?:static[ \t]+)?(?:(?:const|volatile|unsigned|signed)[ \t]+)*"
    r"[A-Za-z_]\w*(?:[ \t]+[A-Za-z_]\w*)*[ \t*]+([A-Za-z_]\w*)[ \t]*(?:\[[^\]\n]*\][ \t]*)*(?:=[^;\n]*)?;[ \t]*$",
    re.M)


def _file_scope_lines(source: str):
    """(start, end) spans of lines at brace depth 0, outside comments and strings."""
    from solver import c89
    masked = c89._mask(source)
    depth, at = 0, 0
    for line in masked.splitlines(keepends=True):
        if depth == 0:
            yield at, at + len(line)
        depth += line.count("{") - line.count("}")
        at += len(line)


def unused(source: str) -> list[tuple[str, int, int]]:
    """(name, start, end) of each file-scope object definition whose name appears nowhere else in the source."""
    out = []
    for start, end in _file_scope_lines(source):
        line = source[start:end]
        m = _DEFINITION.match(line.rstrip("\n"))
        if not m or "(" in line.split("=")[0] or line.lstrip().startswith("#"):
            continue
        name = m.group(1)
        rest = source[:start] + source[end:]
        if not re.search(rf"\b{re.escape(name)}\b", rest):
            out.append((name, start, end))
    return out


def _has_data(obj: bytes) -> bool:
    from solver import byte_certificate as bc
    return bool(set(bc.object_image(obj)["sections"]) & {".bss", ".data", ".sbss", ".sdata"})


def variants(source: str, function: str, *, target_obj: Path, candidate_obj: Path):
    """`(label, candidate)`: all unused file-scope definitions removed, when the candidate alone carries data."""
    try:
        if _has_data(Path(target_obj).read_bytes()) or not _has_data(Path(candidate_obj).read_bytes()):
            return
    except (OSError, ValueError):
        return
    found = unused(source)
    if not found:
        return
    out = source
    for _name, start, end in sorted(found, key=lambda r: -r[1]):
        out = out[:start] + out[end:]
    yield "unused_file_scope_object:" + ",".join(name for name, _, _ in found), out

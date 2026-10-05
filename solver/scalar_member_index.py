"""`p->unkN` on a pointer to a SCALAR becomes `p[N / sizeof(T)]`, which invents nothing.

WHAT THIS OWNS, and why it is not one of the three modules that already touch this diagnostic.
`member reference base type 'T' is not a structure or union` has owners for two shapes and none for
the third:

    solver/void_field_repair       gated on base type 'void' EXACTLY -- a pseudo-field hypothesis
    solver/negative_field_repair   gated on the diagnostic, but proposes only for NEGATIVE offsets
                                   observed in the assembly
    solver/m2c_negative_offset     the `base->unk-N` spelling

So `p->unk10` where `p` is `s32 *` -- a NON-void scalar at a NON-negative offset -- had no owner at
all. Measured on the frozen 200-state frame after the error-limit fix, it is the largest single
blocker among states that are one class from compiling: `member-on-typed-pointer` is the **only**
remaining class in 11 states and appears in 74. Before the ceiling was lifted the same class looked
minor (depth 1, "only visible after a repair"), which is why nothing was pointed at it.

THE REWRITE INVENTS NOTHING, which is what makes it legal under CLAUDE.md's fifth invariant. It
introduces no field name, no struct and no layout. It re-spells one access as the index form of the
SAME byte offset, using only the width of the type the candidate already declares:

    u8  *p;  p->unk1          ->  p[1]          1 / 1
    s16 *p;  p->unk0          ->  p[0]          0 / 2
    s32 *p;  (q + n)->unk10   ->  (q + n)[4]    0x10 / 4
    short a[64];  a.unk2      ->  a[1]          2 / 2      (typedef'd array, via the aka)

If the declared type is wrong this does not make it wronger: the candidate compiles with exactly the
semantics that declaration already asserts, and the offset is preserved to the byte.

WHERE THE FACTS COME FROM. The base type and the exact site are the CHECKER'S OWN WORDS, not a parse
of the candidate: clang names the type and points its column at the `.` or `->` operator itself
(verified against clang 20.1.2 -- `p->unk1` on a 4-space-indented `int a = p->unk1;` reports column
14, which is the `-`). Reading the site from the diagnostic is what makes this safe on bases that are
arbitrary expressions, like `(gRaceCourseSurfaces + sp34)->unk10`.

DECLINES, each named rather than silent:
  * base type `void`            -> `void_field_repair` owns it
  * a negative offset           -> `m2c_negative_offset` / `negative_field_repair` own it
  * offset not a multiple of the width -> the declaration is inconsistent with the access, and
    choosing an index would be choosing a layout. `(gRaceCourseSurfaces + sp34)->unk12` on `s32 *` is
    real and lands here: 0x12 is 18, and 18 % 4 is 2.
  * a base type this module has no width for -> abstain rather than guess
"""
from __future__ import annotations

import hashlib
import re

# Widths as the build's own C sees them: N64 o32, `long` is 32-bit. Only types whose width is a fact
# here; anything else abstains, because a wrong width silently moves the access.
SCALAR_WIDTHS: dict[str, int] = {
    "s8": 1, "u8": 1, "char": 1, "signed char": 1, "unsigned char": 1,
    "s16": 2, "u16": 2, "short": 2, "short int": 2, "unsigned short": 2,
    "s32": 4, "u32": 4, "int": 4, "unsigned int": 4, "long": 4, "unsigned long": 4,
    "f32": 4, "float": 4,
}

_DIAGNOSTIC = re.compile(
    r"^candidate\.c:(?P<line>\d+):(?P<col>\d+):\s*error:\s*member reference base type "
    r"'(?P<type>[^']+)'(?:\s*\(aka '(?P<aka>[^']+)'\))?\s*is not a structure or union",
    re.MULTILINE)

# `unkN` with N in HEX, m2c's spelling. The sign is captured so a negative offset can be DECLINED by
# name instead of silently matching as if it were `unk` + `-2`.
_MEMBER = re.compile(r"\A(?P<op>->|\.)unk(?P<sign>-?)(?P<offset>[0-9A-Fa-f]+)\b")

# `short[64]`, `int[8]` -- a typedef'd array reaches us through the aka, and its ELEMENT width is the
# one that matters. Only a plain scalar element counts; `struct S[4]` is not this module's business.
_ARRAY = re.compile(r"\A(?P<element>[A-Za-z_][\w ]*?)\s*\[\d*\]\Z")


def width_of(declared: str) -> int | None:
    """The element width of a base type the checker named, or None to abstain."""
    name = (declared or "").strip()
    if name in SCALAR_WIDTHS:
        return SCALAR_WIDTHS[name]
    array = _ARRAY.match(name)
    if array:
        return SCALAR_WIDTHS.get(array.group("element").strip())
    return None


def rewrite(source: str, diagnostics: str) -> tuple[str, list[dict]]:
    """Re-spell scalar-based `unkN` accesses as indexes. Returns the source and every decision."""
    report_source = source
    changes: list[dict] = []
    if "member reference base type" not in (diagnostics or ""):
        return source, changes
    lines = source.splitlines(keepends=True)
    # Start offsets, so a (line, column) becomes an absolute position exactly once.
    starts, running = [], 0
    for line in lines:
        starts.append(running)
        running += len(line)

    edits: list[tuple[int, int, str]] = []
    for match in _DIAGNOSTIC.finditer(diagnostics):
        line_no, column = int(match["line"]), int(match["col"])
        declared, aka = match["type"], match["aka"]
        record: dict = {"line": line_no, "column": column, "base_type": declared, "aka": aka}
        if declared == "void" or aka == "void":
            record["declined"] = "void bases belong to solver/void_field_repair"
            changes.append(record)
            continue
        # The spelled type first, its aka second: `AssetHandles` means nothing here and `short[64]` does.
        width = width_of(declared) or width_of(aka or "")
        if not width:
            record["declined"] = f"no width is known for base type {declared!r}"
            changes.append(record)
            continue
        if not 1 <= line_no <= len(lines):
            record["declined"] = "the diagnostic points outside the candidate"
            changes.append(record)
            continue
        position = starts[line_no - 1] + column - 1
        member = _MEMBER.match(source[position:])
        if not member:
            # The column did not land on an `unkN` access. Never edit a site we cannot identify.
            record["declined"] = "the reported column is not an `unkN` member access"
            changes.append(record)
            continue
        if member["sign"] == "-":
            record["declined"] = ("negative offsets belong to solver/m2c_negative_offset and "
                                  "solver/negative_field_repair")
            changes.append(record)
            continue
        offset = int(member["offset"], 16)
        if offset % width:
            record.update(offset=offset, width=width, declined=(
                f"offset 0x{offset:X} is not a multiple of the {width}-byte base type, so an index "
                "would be a layout choice rather than the same address"))
            changes.append(record)
            continue
        index = offset // width
        record.update(offset=offset, width=width, index=index,
                      before=source[position:position + member.end()],
                      after=f"[{index}]",
                      authority="clang named the base type and the site; the offset is unchanged")
        edits.append((position, position + member.end(), f"[{index}]"))
        changes.append(record)

    # Right to left, so an earlier edit cannot move a later one's position.
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    if edits:
        changes.append({"applied": len(edits),
                        "source_sha256": hashlib.sha256(report_source.encode()).hexdigest()})
    return source, changes

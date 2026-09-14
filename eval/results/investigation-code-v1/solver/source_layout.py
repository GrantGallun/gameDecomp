"""Audit partial C structs before trusting their member names or comments.

LLM drafts often declare a shortened struct and annotate each field with the
offset it was *intended* to represent.  C does not honor those comments.  Once
one field has the wrong size, every later member quietly moves while the file
continues to compile.  The differential debugger then sees dozens of unrelated
memory faults even though they share one source cause.

This module deliberately implements only the simple MIPS-o32 layouts used by
isolated candidates: scalar fields, pointers, fixed arrays, and earlier typedef
structs.  Unsupported declarations are declined instead of guessed.  The
authoritative check remains the project compiler/object oracle; this audit is a
source-local warning that a candidate's access scaffold is not trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_TYPEDEF_STRUCT = re.compile(
    r"\btypedef\s+struct(?:\s+[A-Za-z_]\w*)?\s*\{"
    r"(?P<body>.*?)\}\s*(?P<name>[A-Za-z_]\w*)\s*;",
    re.S,
)
_NAMED_STRUCT = re.compile(
    r"(?<!typedef\s)\bstruct\s+(?P<name>[A-Za-z_]\w*)\s*\{"
    r"(?P<body>.*?)\}\s*;",
    re.S,
)
_FIELD = re.compile(
    r"(?m)^[ \t]*(?P<leading>(?:/\*.*?\*/[ \t]*)*)"
    r"(?P<type>(?:(?:const|volatile|signed|unsigned)\s+)*"
    r"(?:struct\s+)?[A-Za-z_]\w*(?:[ \t]*\*)?)"
    r"[ \t]+(?P<name>[A-Za-z_]\w*)"
    r"(?:[ \t]*\[[ \t]*(?P<count>0x[0-9A-Fa-f]+|[0-9]+)[ \t]*\])?"
    r"[ \t]*;(?P<trailing>[ \t]*(?:(?:/\*.*?\*/[ \t]*)|(?://[^\r\n]*))*)$",
)
_HEX = re.compile(r"0x([0-9A-Fa-f]+)")
_OFFSET_NAME = re.compile(r"^(?:unk|field)_?([0-9A-Fa-f]+)$", re.I)


@dataclass(frozen=True)
class OffsetClaim:
    source: str
    offset: int


@dataclass(frozen=True)
class FieldLayout:
    struct: str
    name: str
    type_name: str
    offset: int
    size: int
    claims: tuple[OffsetClaim, ...]

    @property
    def mismatches(self) -> tuple[OffsetClaim, ...]:
        return tuple(claim for claim in self.claims
                     if claim.offset != self.offset)


@dataclass(frozen=True)
class StructLayout:
    name: str
    size: int
    alignment: int
    fields: tuple[FieldLayout, ...]


_SCALARS = {
    "char": (1, 1), "signed char": (1, 1),
    "unsigned char": (1, 1), "s8": (1, 1), "u8": (1, 1),
    "short": (2, 2), "signed short": (2, 2),
    "unsigned short": (2, 2), "s16": (2, 2), "u16": (2, 2),
    "int": (4, 4), "signed int": (4, 4),
    "unsigned int": (4, 4), "long": (4, 4),
    "signed long": (4, 4), "unsigned long": (4, 4),
    "s32": (4, 4), "u32": (4, 4), "f32": (4, 4), "float": (4, 4),
    # IDO's o32 aggregate alignment is at most eight for these simple cases.
    "s64": (8, 8), "u64": (8, 8), "double": (8, 8),
}


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _canonical_type(text: str) -> str:
    text = re.sub(r"\b(?:const|volatile)\b", "", text)
    text = re.sub(r"\bstruct\s+", "", text)
    return " ".join(text.split())


def _claims(leading: str, trailing: str,
            field_name: str) -> tuple[OffsetClaim, ...]:
    claims: list[OffsetClaim] = []
    for label, text in (("leading-comment", leading),
                        ("trailing-comment", trailing)):
        match = _HEX.search(text)
        if match:
            claims.append(OffsetClaim(label, int(match.group(1), 16)))
    encoded = _OFFSET_NAME.match(field_name)
    if encoded:
        claims.append(OffsetClaim("member-name", int(encoded.group(1), 16)))
    out: list[OffsetClaim] = []
    for claim in claims:
        if claim not in out:
            out.append(claim)
    return tuple(out)


def layouts(source: str) -> tuple[StructLayout, ...]:
    """Return every fully understood typedef or named struct layout."""
    known = dict(_SCALARS)
    out: list[StructLayout] = []
    definitions = [
        (match.start(), match) for pattern in (_TYPEDEF_STRUCT, _NAMED_STRUCT)
        for match in pattern.finditer(source)
    ]
    for _start, struct_match in sorted(definitions, key=lambda item: item[0]):
        struct_name = struct_match.group("name")
        body = struct_match.group("body")
        # Normalize declarator spelling for this bounded layout parser only.
        # Function pointers occupy one o32 pointer slot regardless of prototype.
        body = re.sub(
            r"\b(?:void|[A-Za-z_]\w*)\s*\(\s*\*\s*([A-Za-z_]\w*)\s*\)"
            r"\s*\([^;{}]*\)\s*;", r"void * \1;", body)
        body = re.sub(r"(\*)\s*([A-Za-z_]\w*)", r"\1 \2", body)
        matches = list(_FIELD.finditer(body))
        remainder = _FIELD.sub("", body)
        remainder = re.sub(r"/\*.*?\*/|//[^\r\n]*", "", remainder, flags=re.S)
        if remainder.strip():
            # Bitfields, nested aggregates, macros, multiple declarators, etc.
            # must never disappear while later fields get fabricated offsets.
            continue
        offset = 0
        aggregate_alignment = 1
        fields: list[FieldLayout] = []
        understood = True
        for field_match in matches:
            type_name = _canonical_type(field_match.group("type"))
            if "*" in type_name:
                size, alignment = 4, 4
            else:
                item = known.get(type_name)
                if item is None:
                    understood = False
                    break
                size, alignment = item
            count = int(field_match.group("count") or "1", 0)
            offset = _align(offset, alignment)
            field_size = size * count
            fields.append(FieldLayout(
                struct_name, field_match.group("name"), type_name, offset,
                field_size, _claims(field_match.group("leading") or "",
                                    field_match.group("trailing") or "",
                                    field_match.group("name"))))
            offset += field_size
            aggregate_alignment = max(aggregate_alignment, alignment)
        if not understood or not fields:
            continue
        total = _align(offset, aggregate_alignment)
        layout = StructLayout(
            struct_name, total, aggregate_alignment, tuple(fields))
        out.append(layout)
        known[struct_name] = (total, aggregate_alignment)
    return tuple(out)


def mismatches(source: str) -> tuple[FieldLayout, ...]:
    """Fields whose claimed offset disagrees with the actual C ABI layout."""
    return tuple(field for layout in layouts(source) for field in layout.fields
                 if field.mismatches)


def prompt_warning(source: str, max_fields: int = 16) -> str:
    """Compact evidence block suitable for a repair prompt."""
    rows = mismatches(source)
    if not rows:
        return ""
    rendered = []
    for field in rows[:max_fields]:
        claims = ", ".join(
            f"{claim.source}=0x{claim.offset:x}" for claim in field.claims)
        rendered.append(
            f"- `{field.struct}.{field.name}` is laid out at `0x{field.offset:x}`"
            f" by MIPS-o32 C, while the draft claims {claims}.")
    if len(rows) > max_fields:
        rendered.append(f"- ...and {len(rows) - max_fields} more mismatches.")
    rendered.append(
        "- Treat the entire partial struct as untrusted. Comments and member "
        "names do not constrain C layout. Rebind accesses to target-observed "
        "offsets using a project header or explicit byte-offset lvalues before "
        "diagnosing each downstream memory fault separately.")
    return "LOCAL PARTIAL-STRUCT LAYOUT AUDIT:\n" + "\n".join(rendered)

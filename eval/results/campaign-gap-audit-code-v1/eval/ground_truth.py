"""Parse known-good struct layouts and function signatures from a finished decomp.

SBK1 is 100% matched, so its headers are ground truth: `/* 0x24 */ s16 state;`
is not a guess, it is the answer. That makes the repo a test oracle for any
analysis pass we write -- correct code cannot contradict itself, so every
disagreement is either a documented quirk or a bug in us.

This module only reads. It never writes to the knowledge base: ground truth is
for *checking* the miner, never for feeding it. Letting it flow into the KB
would be teaching to the test, and would invalidate every number afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Primitive widths under the N64 O32 ABI.
PRIMITIVE_WIDTH = {
    "u8": 1, "s8": 1, "char": 1, "signed char": 1, "unsigned char": 1, "bool": 1,
    "u16": 2, "s16": 2, "short": 2, "unsigned short": 2,
    "u32": 4, "s32": 4, "int": 4, "unsigned int": 4, "long": 4, "unsigned long": 4,
    "f32": 4, "float": 4,
    "u64": 8, "s64": 8, "f64": 8, "double": 8, "long long": 8,
}
POINTER_WIDTH = 4

STRUCT_OPEN_RE = re.compile(
    r"^\s*typedef\s+struct\s*([A-Za-z_]\w*)?\s*\{|^\s*struct\s+([A-Za-z_]\w*)\s*\{")
STRUCT_CLOSE_RE = re.compile(r"^\s*\}\s*([A-Za-z_]\w*)?\s*;")
FIELD_RE = re.compile(
    r"^\s*/\*\s*(0x[0-9A-Fa-f]+)\s*\*/\s*"      # offset comment
    r"(.+?)\s*"                                  # type + name + arrays
    r";\s*(?://.*)?$"
)
ARRAY_RE = re.compile(r"\[\s*(\w+)\s*\]")

# `void updateFoo(Bar *arg0) {` and friends
SIGNATURE_RE = re.compile(
    r"^(?:static\s+)?[A-Za-z_]\w*\s*\**\s*"      # return type
    r"([A-Za-z_]\w*)\s*\(\s*([^)]*?)\s*\)\s*\{", # name ( params ) {
    re.MULTILINE,
)
PARAM_PTR_RE = re.compile(r"^\s*(?:const\s+)?(?:struct\s+)?([A-Za-z_]\w*)\s*\*\s*\w+\s*$")


@dataclass
class Field:
    offset: int
    type_name: str
    name: str
    elem_count: int
    is_pointer: bool


@dataclass
class Struct:
    name: str
    fields: list[Field] = field(default_factory=list)


def _parse_field(offset: int, decl: str) -> Field | None:
    """Turn `s16 state` / `u32 words[16]` / `Mtx *matrix` into a Field."""
    counts = [c for c in ARRAY_RE.findall(decl)]
    decl = ARRAY_RE.sub("", decl).strip()

    elem_count = 1
    for c in counts:
        try:
            elem_count *= int(c, 0)
        except ValueError:
            return None                      # symbolic array bound; skip honestly

    is_pointer = "*" in decl
    decl = decl.replace("*", " ").strip()

    parts = decl.split()
    if len(parts) < 2:
        return None
    name = parts[-1]
    type_name = " ".join(parts[:-1]).replace("struct ", "").strip()

    return Field(offset=offset, type_name=type_name, name=name,
                 elem_count=elem_count, is_pointer=is_pointer)


def parse_structs(roots: list[Path]) -> dict[str, Struct]:
    """Collect every struct with offset-annotated fields."""
    structs: dict[str, Struct] = {}

    for root in roots:
        for path in list(root.rglob("*.h")) + list(root.rglob("*.c")):
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue

            current: Struct | None = None
            depth = 0
            for line in lines:
                if current is None:
                    m = STRUCT_OPEN_RE.match(line)
                    if m:
                        current = Struct(name=(m.group(1) or m.group(2) or ""))
                        depth = line.count("{") - line.count("}")
                    continue

                # Track braces rather than matching the first `};`. Anonymous
                # unions and structs -- which is how these decomps express
                # overlaid fields -- close with a bare `};` that would
                # otherwise end the enclosing struct early, silently discarding
                # every field after the union.
                depth += line.count("{") - line.count("}")

                if depth <= 0:
                    close = STRUCT_CLOSE_RE.match(line)
                    name = (close.group(1) if close else None) or current.name
                    if name and current.fields:
                        structs[name] = Struct(name=name, fields=current.fields)
                    current = None
                    continue

                fm = FIELD_RE.match(line)
                if fm:
                    parsed = _parse_field(int(fm.group(1), 16), fm.group(2))
                    if parsed:
                        current.fields.append(parsed)

    return structs


def flatten(struct_name: str, structs: dict[str, Struct],
            depth: int = 0) -> dict[int, set[int]]:
    """Flatten a struct to {offset: {acceptable widths}}.

    The value is a SET because unions make an offset legitimately polymorphic.
    RaceUiDualCounterActor overlays `s8 row` and `s16 alpha18` at 0x18, so both
    a `lb` and a `lh` there are correct. Keeping one width per offset silently
    drops the other member and manufactures a false disagreement -- which is
    exactly how this was found.

    Anonymous unions need no special parsing: their members carry the same
    offset comment as the fields they overlay, so collecting into a set handles
    them for free.

    Only offsets whose width we can determine appear. A field of unknown type
    contributes nothing rather than a guess.
    """
    if depth > 4 or struct_name not in structs:
        return {}

    layout: dict[int, set[int]] = {}
    pad_offsets: set[int] = set()
    real_offsets: set[int] = set()

    for f in structs[struct_name].fields:
        # `u8 pad0[0x18]` usually means "we do not know what lives here". But
        # inside a union it is also a legitimate alternate view: SBK1 really
        # does write `arg0->pad18[4] = 2`, producing a one-byte store at 0x1c
        # where another arm declares an s16. So padding contributes its width
        # as *acceptable*, and offsets covered ONLY by padding are reported as
        # uncheckable rather than counted as agreement -- otherwise unfalsifiable
        # regions would inflate the accuracy number.
        is_pad = f.name.lower().startswith("pad")
        if f.is_pointer:
            width = POINTER_WIDTH
        elif f.type_name in PRIMITIVE_WIDTH:
            width = PRIMITIVE_WIDTH[f.type_name]
        elif f.type_name in structs:
            inner, inner_pad = flatten(f.type_name, structs, depth + 1)
            if not inner:
                continue
            last = max(inner)
            stride = last + max(inner[last])
            for i in range(f.elem_count):
                base = f.offset + i * stride
                for off, widths in inner.items():
                    layout.setdefault(base + off, set()).update(widths)
                pad_offsets.update(base + off for off in inner_pad)
                real_offsets.update(base + off for off in inner if off not in inner_pad)
            continue
        else:
            continue                          # unknown type: contribute nothing

        for i in range(f.elem_count):
            off = f.offset + i * width
            layout.setdefault(off, set()).add(width)
            (pad_offsets if is_pad else real_offsets).add(off)

    # An offset is only "padding" if no named field also describes it.
    return layout, pad_offsets - real_offsets


def parse_signatures(roots: list[Path]) -> dict[str, list[str | None]]:
    """Map function name -> list of parameter struct types (None if not a struct ptr)."""
    sigs: dict[str, list[str | None]] = {}

    for root in roots:
        for path in root.rglob("*.c"):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for m in SIGNATURE_RE.finditer(text):
                name, params = m.group(1), m.group(2).strip()
                if not params or params == "void":
                    sigs[name] = []
                    continue
                types: list[str | None] = []
                for p in params.split(","):
                    pm = PARAM_PTR_RE.match(p)
                    types.append(pm.group(1) if pm else None)
                sigs[name] = types

    return sigs


def load(repo: Path):
    roots = [repo / "include", repo / "src"]
    structs = parse_structs(roots)
    sigs = parse_signatures([repo / "src"])
    return structs, sigs


if __name__ == "__main__":
    import sys
    repo = Path(sys.argv[1]).expanduser()
    structs, sigs = load(repo)
    resolvable = {n: flatten(n, structs)[0] for n in structs}
    resolvable = {n: v for n, v in resolvable.items() if v}
    pads = {n: flatten(n, structs)[1] for n in resolvable}
    print(f"structs parsed      : {len(structs)}")
    print(f"structs flattened   : {len(resolvable)}")
    print(f"total known offsets : {sum(len(v) for v in resolvable.values())}")
    print(f"  of which padding  : {sum(len(v) for v in pads.values())}")
    print(f"signatures parsed   : {len(sigs)}")
    ptr_first = sum(1 for v in sigs.values() if v and v[0])
    print(f"  with struct param0: {ptr_first}")

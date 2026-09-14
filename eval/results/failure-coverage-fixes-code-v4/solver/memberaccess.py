"""Rewrite member access through a byte pointer into indexing.

m2c sometimes declares a parameter as a primitive pointer and then emits struct
member accesses through it:

    s32 Fcutoff(PlayerCommandState *arg0, u8 *arg1) {
        arg0->unkC2 = (arg1->unk0 << 8) | arg1->unk1;

`u8` is a real build type, so solver/typedecl.py correctly skips that parameter
-- there is no undeclared type to declare -- and the draft still does not
compile. Measured 2026-09-01 over the never-compiled leaves, this is the single
largest surviving blocker after typedecl: 12 of 35 planned drafts fail with
`Selector requires struct/union pointer as left hand side`, all on a byte
pointer, all in the libmus command-stream family (Fcutoff, Fdrums, Fgoto).

The fix is NOT to invent a struct. Changing `u8 *` to `T *` would silently
rescale the pointer arithmetic these functions depend on -- `return (s32)(arg1
+ 2)` advances two BYTES through a command stream, and would become two structs.
m2c encodes the offset in the member name (`unkC2` is offset 0xC2), so for a
one-byte element type the offset IS the index and `arg1->unk1` is exactly
`arg1[1]`.

Restricted to one-byte element types on purpose. For a wider element the offset
would have to divide the element size, and an off-by-one there is a wrong
constant rather than a compile error -- the failure mode this project exists to
avoid. Anything else declines.
"""

from __future__ import annotations

import re

from solver import typedecl, typepool

# The element types whose size is exactly one byte, so offset == index.
BYTE_POINTERS = frozenset({"u8", "s8", "char", "uchar"})


def _member_pattern(variable: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(variable) + r"\s*->\s*([A-Za-z_]\w*)")


def plan(code: str, func: str) -> list[dict]:
    """One entry per byte-pointer parameter whose members are all offsets."""
    out = []
    for _index, type_name, var in typedecl.pointer_parameters(code, func):
        if type_name not in BYTE_POINTERS:
            continue
        members = typedecl.members_used(code, {var})
        if not members:
            continue
        offsets = typepool.member_offsets(members)
        if len(offsets) != len(members):
            continue          # a real member name carries no offset: decline
        out.append({"variable": var, "type": type_name,
                    "mapping": {m: offsets[m] for m in members}})
    return out


def apply(code: str, plans: list[dict]) -> str:
    for entry in plans:
        mapping = entry["mapping"]

        def replace(match, mapping=mapping, var=entry["variable"]):
            member = match.group(1)
            if member not in mapping:
                return match.group(0)
            return f"{var}[{mapping[member]}]"

        code = _member_pattern(entry["variable"]).sub(replace, code)
    return code


def rewrite(code: str, func: str) -> tuple[str, list[dict]]:
    plans = plan(code, func)
    return apply(code, plans), plans

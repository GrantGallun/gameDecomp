"""Pool a struct's observed offsets across every function that names its type.

`structgen.layout` answers per function, so a parameter's layout is only ever
as complete as the one function being compiled. That is why `typedecl` declines
so often: it refuses when a draft names more members than the binary has
offsets for, and a single function usually touches only a handful.

Measured 2026-09-01 over the never-compiled leaves: `PlayerCommandState` is
named by 15 drafts and pools to 36 distinct offsets, against 15 for the best
single function -- 2.4x more layout, closely matching the 2.7x that
`miner/globals_layout.py` reports for globals. Of 42 declines examined, 13 were
"more members than offsets" and 8 "no evidence for that param"; both are what
pooling addresses.

WHAT IS AND IS NOT A FACT HERE. Each offset is evidence: the binary encodes it.
The GROUPING is not. Two functions' `param0` are the same object only if the
types really are the same, and the only thing asserting that is m2c's own
naming. So a pooled struct is a HYPOTHESIS, held the way this project holds
hypotheses: it changes nothing on its own, it is handed to the compiler, and
byte-exact comparison decides. That is the same caution `structgen`'s docstring
records as "base register is not object identity", applied one level up.

Deliberately excluded: types the build already declares. Pooling `Gfx` or
`OSMesgQueue` would fabricate a rival definition of a type that already exists.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from solver import structgen, typedecl, workspace

# m2c names an unknown member after its HEX offset: unk0, unk1, unkC2, unkFD.
UNK_MEMBER = re.compile(r"^unk([0-9A-Fa-f]+)$")


def type_uses(repo: Path, functions: list[str]) -> dict[str, list[tuple[str, int]]]:
    """type name -> [(function, parameter index)] across every bootstrapped draft."""
    uses: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for func in functions:
        draft = workspace.m2c_draft(Path(repo) / "nonmatchings" / func)
        if not draft.strip():
            continue
        for index, type_name, _var in typedecl.pointer_parameters(draft, func):
            uses[type_name].append((func, index))
    return dict(uses)


def pool(conn, uses: dict[str, list[tuple[str, int]]], known_types: set[str],
         ) -> dict[str, list[tuple[int, int, str]]]:
    """type name -> merged [(offset, width, ctype)], widest access winning.

    Same merge rule as structgen.layout: a narrower field cannot hold a wider
    one, so the widest observed access at an offset is the one that survives.
    """
    out: dict[str, list[tuple[int, int, str]]] = {}
    for type_name, sites in uses.items():
        if type_name in known_types:
            continue                       # never redefine a build type
        slots: dict[int, tuple[int, str]] = {}
        for func, index in sites:
            for off, width, ctype in (structgen.layout(conn, func)
                                      .get(f"param{index}") or []):
                prev = slots.get(off)
                if prev is None or width > prev[0]:
                    slots[off] = (width, ctype)
        if slots:
            out[type_name] = [(o, w, t) for o, (w, t) in sorted(slots.items())]
    return out


def member_offsets(members: list[str]) -> dict[str, int]:
    """`unkC2` -> 0xC2. Only m2c's offset-encoded names; anything else is a
    real name we must not reinterpret as a number."""
    found = {}
    for name in members:
        match = UNK_MEMBER.match(name)
        if match:
            found[name] = int(match.group(1), 16)
    return found


def named_fields(fields: list[tuple[int, int, str]], members: list[str],
                 ) -> dict[int, str] | None:
    """Place members at the offsets their own names encode, when they encode one.

    Zipping members onto offsets in source order -- what typedecl does today --
    is a guess that gets worse as the pool grows, because a pooled struct has
    offsets no single function touches. When m2c has encoded the offset IN the
    member name there is nothing to guess: unkC2 belongs at 0xC2. Returns None
    when a name points at an offset the pool does not contain, which means the
    pool and the draft disagree and neither should be trusted.
    """
    encoded = member_offsets(members)
    if len(encoded) != len(members):
        return None                        # some member is a real name
    valid = {off for off, _w, _t in fields}
    if not all(off in valid for off in encoded.values()):
        return None
    return {off: name for name, off in encoded.items()}

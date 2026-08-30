"""Repair struct layout from the ORACLE'S OWN DIFF, not from the evidence tier.

Every layout intervention so far asked the knowledge base where a field belongs:
per-function accesses, then whole-program global objects, then positional and
subsequence alignment. All produced 0 matches, and the reason was always the
same -- the KB knows which offsets the binary touches, but nothing maps a
particular DECLARATION to a particular offset.

The diff already contains that mapping, stated exactly:

    -lbu    v1,0x24(a0)        the target reads this field at 0x24
    +lbu    v1,0(a0)           the candidate put it at 0

That is not evidence to be interpreted. It is a counterexample naming its own
fix: whatever field the candidate placed at 0 belongs at 0x24. Same base
register, same opcode, same destination -- only the offset differs.

updateRaceSplitscreenSelectPlayerCountIcons sits at 99.436 with zero structural
faults and 27 offset faults of exactly this shape, so the whole residual is a
padding problem whose answer is written in the diff.

WHY THIS IS STILL A PROPOSAL, NOT A FACT
    The mapping is only as good as the pairing of diff lines, and a diff is not
    an alignment. Two accesses to different fields can pair up by accident and
    produce a contradictory constraint. So conflicting constraints are dropped
    rather than guessed between, and the result is handed to the oracle like
    any other candidate. A wrong repair fails to verify and costs one compile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# `lbu v1,0x24(a0)` -- opcode, destination, offset, base
MEM = re.compile(
    r"^([a-z][a-z0-9.]*)\s+(\$?\w+),\s*(-?(?:0x)?[0-9a-f]+)\((\$?\w+)\)$")
DECL = re.compile(r"^(?P<indent>[ \t]*)(?P<type>[A-Za-z_][\w ]*?)\s+"
                  r"(?P<ptr>\**)\s*(?P<name>[A-Za-z_]\w*)\s*"
                  r"(?:\[\s*(?P<count>0[xX][0-9A-Fa-f]+|\d+)\s*\])?\s*;",
                  re.M)
SIZEOF = {"u8": 1, "s8": 1, "char": 1, "u16": 2, "s16": 2, "short": 2,
          "u32": 4, "s32": 4, "int": 4, "long": 4, "float": 4, "f32": 4,
          "u64": 8, "s64": 8, "f64": 8, "double": 8}


def _num(text: str) -> int | None:
    try:
        return int(text, 16) if text.lower().startswith(("0x", "-0x")) \
            else int(text)
    except ValueError:
        return None


@dataclass
class Constraint:
    produced: int
    expected: int


def constraints(diff: str) -> tuple[dict[int, int], list[int]]:
    """{produced offset -> expected offset}, PER BASE REGISTER then merged.

    Two corrections learned the hard way on
    updateRaceSplitscreenSelectPlayerCountIcons, whose diff touches a0, a3 and
    v0:

      - accesses through DIFFERENT base registers are generally different
        objects, so merging them produced a non-monotonic mapping (0 -> 0x24
        from one base and 4 -> 0x18 from another) that describes no single
        struct. Constraints are now grouped by base and a base is used only if
        its own constraints are internally consistent AND order-preserving.
      - a diff is not an alignment. zip() pairs the Nth removed line with the
        Nth added line, which drifts as soon as one side has an unpaired
        instruction. A base whose mapping is not monotonically increasing is
        evidence of exactly that drift, so it is dropped rather than trusted.

    Returns (mapping, dropped_bases).
    """
    minus = [l[1:].strip() for l in diff.splitlines()
             if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:].strip() for l in diff.splitlines()
            if l.startswith("+") and not l.startswith("+++")]

    per_base: dict[str, dict[int, set]] = {}
    for a, b in zip(minus, plus):
        ma, mb = MEM.match(a), MEM.match(b)
        if not (ma and mb):
            continue
        # a different opcode is a WIDTH problem, not a position one; a
        # different base is a different object
        if ma.group(1) != mb.group(1) or ma.group(4) != mb.group(4):
            continue
        want, got = _num(ma.group(3)), _num(mb.group(3))
        if want is None or got is None or want == got:
            continue
        per_base.setdefault(mb.group(4), {}).setdefault(got, set()).add(want)

    mapping: dict[int, int] = {}
    dropped: dict[str, str] = {}
    for base, seen in per_base.items():
        if any(len(w) > 1 for w in seen.values()):
            dropped[base] = "contradictory"
            continue
        m = {g: next(iter(w)) for g, w in seen.items()}
        got_order = sorted(m)
        if [m[g] for g in got_order] != sorted(m[g] for g in got_order):
            # An earlier field must land AFTER a later one. Padding only moves
            # fields later and preserves their order, so this is unfixable
            # here. Two causes are indistinguishable from the diff alone --
            # the fields genuinely need reordering, or the line pairing
            # drifted -- and both mean "do not pad", so they share a label.
            dropped[base] = "non-monotonic"
            continue
        for g, w in m.items():
            if mapping.get(g, w) != w:
                dropped[base] = "conflicts with another base"
                break
        else:
            mapping.update(m)
    return mapping, dropped


STRUCT_BODY = re.compile(
    r"\b(?:typedef\s+)?(?:struct|union)\b[^{;]*\{", re.M)


def _struct_regions(body: str) -> list:
    """(start, end) of each struct/union body, braces balanced.

    Walking every declaration in the FILE with one running offset counted
    `typedef unsigned char u8;` as a field at offset 0 and the function's own
    locals as fields too, so every computed offset was meaningless. Fields only
    exist inside a struct body.
    """
    out = []
    for m in STRUCT_BODY.finditer(body):
        depth, i = 0, m.end() - 1
        while i < len(body):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if i < len(body):
            out.append((m.end(), i))
    return out


def _align(cursor: int, size: int) -> int:
    """MIPS aligns a field to its own width."""
    a = min(size, 4) if size else 1
    return (cursor + a - 1) // a * a


def _fields(body: str) -> list:
    """Declarations INSIDE struct bodies, with their aligned running offset."""
    out = []
    for start, end in _struct_regions(body):
        cursor = 0
        for m in DECL.finditer(body, start, end):
            ctype = m.group("type").strip().split()[-1]
            n = int(m.group("count"), 0) if m.group("count") else 1
            unit = 4 if m.group("ptr") else SIZEOF.get(ctype, 0)
            if not unit:
                continue
            cursor = _align(cursor, unit)
            out.append((m, cursor, unit * n))
            cursor += unit * n
    return out


def apply_constraints(body: str, mapping: dict[int, int]) -> tuple[str, bool]:
    """Insert padding so each constrained field lands on its expected offset.

    Walks declarations in order, and when one sits at a produced offset the
    diff has an opinion about, pads ahead of it to reach the expected offset.
    Later fields shift with it, which is the point -- a struct missing an early
    pad has every subsequent field wrong, and that is precisely the 27-fault
    shape this exists to fix.
    """
    fields = _fields(body)
    if not fields or not mapping:
        return body, False

    edits: list[tuple[int, str]] = []
    shift = 0
    for m, cursor, size in fields:
        here = cursor + shift
        want = mapping.get(cursor)
        if want is None or want <= here:
            continue
        gap = want - here
        indent = m.group("indent")
        edits.append((m.start(),
                      f"{indent}char dpad{here:02x}[{gap:#x}];\n"))
        shift += gap

    if not edits:
        return body, False
    out = body
    for start, text in reversed(edits):
        out = out[:start] + text + out[start:]
    return out, True


def order_violation(mapping: dict[int, int]) -> bool:
    """True when the constraints require fields to swap places.

    Inserting padding can only move fields LATER and preserves their order, so
    a mapping where an earlier field must land after a later one describes a
    reordering, not a padding fix.

    updateRaceSplitscreenSelectPlayerCountIcons is exactly this: the field at 1
    must reach 0x26 while the field at 2 must reach 0x25. No padding produces
    that. Detecting it is the useful outcome -- it says the candidate's field
    ORDER is wrong, which is a different repair and a different tool.
    """
    keys = sorted(mapping)
    vals = [mapping[k] for k in keys]
    return vals != sorted(vals)


def repair(code: str, diff: str) -> tuple[str, bool, dict]:
    """Full pass: read the diff's constraints and apply them. (code, changed, info).

    Declines when the constraints require reordering. Emitting a struct that
    satisfies half a contradictory constraint set is worse than emitting
    nothing: it compiles, scores differently, and hides the real diagnosis.
    """
    mapping, dropped = constraints(diff)
    info = {"constraints": len(mapping), "dropped": len(dropped),
            "needs_reorder": any(r == "non-monotonic"
                                 for r in dropped.values())}
    if info["needs_reorder"] and not mapping:
        return code, False, info
    if order_violation(mapping):
        info["needs_reorder"] = True
        return code, False, info
    out, changed = apply_constraints(code, mapping)
    return out, changed, info

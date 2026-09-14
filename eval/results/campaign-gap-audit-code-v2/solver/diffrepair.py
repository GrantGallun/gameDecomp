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
# The optional leading comment is load bearing. Candidates routinely annotate
# padding inline:
#
#     /* 0x00 .. 0x17 */ char pad0[0x18];
#
# and anchoring at `^[ \t]*` made every such declaration invisible, so the pads
# were not counted and every field after one got the wrong running offset. On
# updateRaceSetupFourPlayerOption that put `state` at 0x02 instead of 0x1C, and
# the diff's constraint (0x1b -> 0x1c) then matched no field at all.
DECL = re.compile(r"^(?P<indent>[ \t]*)(?:/\*[^*]*(?:\*(?!/)[^*]*)*\*/[ \t]*)?"
                  r"(?P<type>[A-Za-z_][\w ]*?)\s+"
                  r"(?P<ptr>\**)\s*(?P<name>[A-Za-z_]\w*)\s*"
                  r"(?:\[\s*(?P<count>0[xX][0-9A-Fa-f]+|\d+)\s*\])?\s*;",
                  re.M)
SIZEOF = {"u8": 1, "s8": 1, "char": 1, "u16": 2, "s16": 2, "short": 2,
          "u32": 4, "s32": 4, "int": 4, "long": 4, "float": 4, "f32": 4,
          "u64": 8, "s64": 8, "f64": 8, "double": 8}



def _already_declared(text: str, name: str) -> bool:
    """Has this exact padding field already been inserted?

    The name is derived from the position it fills, so re-running the pass on
    an already-repaired body recomputes the same position and inserts a second
    `char dpad00[0x2];` next to the first. IDO rejects that with
    `Duplicate member 'dpad00'`, which accounted for 143 non-compiling attempts
    -- 3.6% of every compile failure ever logged -- all of them self-inflicted
    rather than a bad candidate. The pass is now idempotent.

    Substring, not a regex, on purpose: the emitted form is always
    `char <name>[`, so no word-boundary escape is needed -- and an escape here
    is exactly what this session corrupted into a literal 0x08 twice.
    """
    return (name + "[") in text


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


OFFSET_HOLE = re.compile(r"(-?(?:0x)?[0-9a-f]+)(\()")


def _streams(diff: str) -> tuple[list[str], list[str]]:
    """Reconstruct the target and candidate instruction streams from the diff.

    The oracle emits a unified diff WITH context (61 context lines against 30
    changed on the reference case), so both sides can be rebuilt in full:
    a context line belongs to both, a '-' line to the target, a '+' line to the
    candidate. Regions the diff skips between hunks are identical on both sides
    and can be concatenated away without disturbing the alignment.
    """
    target: list[str] = []
    cand: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@")) or not line:
            continue
        body = line[1:].strip()
        if not body:
            continue
        if line[0] == " ":
            target.append(body)
            cand.append(body)
        elif line[0] == "-":
            target.append(body)
        elif line[0] == "+":
            cand.append(body)
    return target, cand


def _blank_offset(instr: str) -> str:
    """`lbu v1,0x24(a0)` -> `lbu v1,OFF(a0)`.

    The point of the whole alignment: two instructions that differ ONLY in
    their offset become identical here, so the matcher pairs them as equal and
    their real offsets can then be compared. Anything still unequal is a
    genuine structural difference, not a moved field.
    """
    return OFFSET_HOLE.sub(r"OFF\2", instr)


def aligned_pairs(diff: str) -> list[tuple[str, str]]:
    """(target, candidate) instruction pairs that differ only in an offset.

    Replaces positional zip(minus, plus), which is not an alignment: a single
    unpaired instruction on either side made every later pair drift, and a
    drifted pair is indistinguishable from a genuine field reordering. Aligning
    the normalized streams removes that failure mode entirely -- the matcher
    knows which instruction corresponds to which.
    """
    from solver import alignment

    # The old SequenceMatcher path silently chose one pairing among repeated
    # opcode runs.  Layout changes every later field, so an arbitrary pairing
    # is too dangerous.  The block-aware aligner returns only unambiguous pairs
    # with identical opcode, destination register, and base register.
    return alignment.safe_offset_pairs(diff)


MEM_OP = re.compile(r"^(l|s)(b|h|w)(u?)(c1)?\b")


def _blank_memop(instr: str) -> str:
    """`sw t1,0x1c(t2)` and `sb t1,0x1c(t2)` both -> `S t1,OFF(t2)`.

    aligned_pairs() blanks only the offset, so a pair differing in OPCODE never
    lands in an equal block and width faults were invisible to it -- the width
    constraints could never fire. Collapsing a memory op to its direction
    (load/store) makes those pairs align, and the real opcodes are then
    compared to recover the intended width.
    """
    return _blank_offset(MEM_OP.sub(lambda m: m.group(1).upper(), instr))


def aligned_pairs_loose(diff: str) -> list[tuple[str, str]]:
    """Pairs that differ only in offset OR in access width."""
    from solver import alignment

    return alignment.safe_memory_pairs(diff)


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
    per_base: dict[str, dict[int, set]] = {}
    for a, b in aligned_pairs(diff):
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
        # A non-monotonic mapping is KEPT. It used to be dropped, because with
        # positional zip() pairing it was indistinguishable from line drift --
        # but pairs now come from a real alignment of the two instruction
        # streams, so "an earlier field must land after a later one" is a
        # statement about the struct rather than an artefact of the diff. It
        # means the fields need REORDERING, which reorder_fields() handles and
        # padding cannot. Dropping it here discarded the very signal the
        # alignment work existed to recover.
        for g, w in m.items():
            if mapping.get(g, w) != w:
                dropped[base] = "conflicts with another base"
                break
        else:
            mapping.update(m)
    return mapping, dropped


# Excluding parentheses matters: without it, `void f(struct X *row)\n{` matched
# and the FUNCTION BODY was parsed as a struct, so its locals -- new_var, i,
# moved -- were counted as fields at offsets 0, 4, 8. A struct's opening brace
# never follows a ')'.
STRUCT_BODY = re.compile(
    r"\b(?:typedef\s+)?(?:struct|union)\b[^{;()]*\{", re.M)


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
        name = f"dpad{here:02x}"
        if _already_declared(body, name):
            continue
        edits.append((m.start(), f"{indent}char {name}[{gap:#x}];\n"))
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


# Access width implied by the opcode. `sw` writes four bytes, `sb` one, so a
# target/candidate pair that agrees on offset and base but differs in opcode is
# stating the field's TYPE, not its position.
OPCODE_WIDTH = {
    "lb": 1, "lbu": 1, "sb": 1,
    "lh": 2, "lhu": 2, "sh": 2,
    "lw": 4, "sw": 4, "lwc1": 4, "swc1": 4,
}
# Unsigned loads tell us the field is unsigned; signed loads say signed. A
# STORE says nothing about signedness, so it only constrains the width.
UNSIGNED_OPS = {"lbu", "lhu"}
SIGNED_OPS = {"lb", "lh", "lw"}
WIDTH_TYPE = {(1, True): "u8", (1, False): "s8",
              (2, True): "u16", (2, False): "s16",
              (4, True): "u32", (4, False): "s32"}


def width_constraints(diff: str) -> dict[int, tuple[int, bool | None]]:
    """{offset -> (width, unsigned)} from pairs differing only in opcode.

        -sw t1,0x1c(t2)      the target writes FOUR bytes at 0x1c
        +sb t1,0x1c(t2)      the candidate declared a one-byte field

    repad and reorder_fields can move a field; neither can retype one, so this
    fault class had no repair at all despite appearing in roughly fifteen
    functions. As with offsets, the diff states the answer rather than merely
    hinting at it.

    `unsigned` is None when only stores were seen, because a store carries no
    signedness information and guessing one would change the declared type on
    no evidence.
    """
    out: dict[int, tuple[int, bool | None]] = {}
    conflict: set[int] = set()
    for a, b in aligned_pairs_loose(diff):
        ma, mb = MEM.match(a), MEM.match(b)
        if not (ma and mb):
            continue
        if ma.group(3) != mb.group(3) or ma.group(4) != mb.group(4):
            continue                      # different slot, not a retype
        want = OPCODE_WIDTH.get(ma.group(1))
        got = OPCODE_WIDTH.get(mb.group(1))
        if want is None or got is None:
            continue
        sign = (True if ma.group(1) in UNSIGNED_OPS
                else False if ma.group(1) in SIGNED_OPS else None)
        got_sign = (True if mb.group(1) in UNSIGNED_OPS
                    else False if mb.group(1) in SIGNED_OPS else None)
        # Equal widths used to end it here, which silently discarded every
        # SIGNEDNESS fault: lb and lbu are both one byte, so `-lb` against
        # `+lbu` on one offset produced no constraint at all even though the
        # signedness is right there in the opcode. u8 where the target reads
        # s8 is an ordinary decompilation error and it had no repair.
        if want == got and (sign is None or got_sign is None
                            or sign == got_sign):
            continue
        off = _num(ma.group(3))
        if off is None:
            continue
        prev = out.get(off)
        if prev is not None and prev[0] != want:
            conflict.add(off)             # two widths for one field
            continue
        if prev is not None and prev[1] is not None and sign is None:
            sign = prev[1]                # keep a load's signedness over a store
        out[off] = (want, sign)
    for off in conflict:
        out.pop(off, None)
    return out


def apply_widths(body: str, widths: dict[int, tuple[int, bool | None]]
                 ) -> tuple[str, bool]:
    """Retype fields whose declared width contradicts the target's access.

    Only scalar declarations are touched, and only when the size actually
    changes. Arrays are left alone: `s16 iconX[5]` accessed as a word is a
    different question -- possibly the element type, possibly the index
    arithmetic -- and retyping it would be guessing between them.
    """
    changed = False
    out = body
    for m, off, size in reversed(_fields(body)):
        want = widths.get(off)
        if want is None or m.group("count"):
            continue
        width, unsigned = want
        ctype = m.group("type").strip().split()[-1]
        if ctype not in SIZEOF:
            continue                      # a named struct type, not a scalar
        # A same-width retype is still a retype when the SIGNEDNESS differs;
        # returning early on size alone is what made signedness unrepairable.
        if size == width and (unsigned is None
                              or unsigned == ctype.startswith("u")):
            continue
        if unsigned is None:
            unsigned = ctype.startswith("u")
        new_type = WIDTH_TYPE.get((width, unsigned))
        if not new_type:
            continue
        decl = m.group(0)
        fixed = decl.replace(ctype, new_type, 1)
        if fixed != decl:
            out = out[:m.start()] + fixed + out[m.end():]
            changed = True
    return out, changed


def reorder_fields(body: str, mapping: dict[int, int]) -> tuple[str, bool]:
    """Rewrite a struct so every field sits on its diff-stated offset.

    Padding alone cannot satisfy a non-monotonic constraint set -- it only
    moves fields later and preserves their order. On
    updateRaceSplitscreenSelectPlayerCountIcons the diff asks for
    {0 -> 0x24, 1 -> 0x26, 2 -> 0x25, 4 -> 0x18}, which reads as:

        iconX   0x18      state   0x24      spawnTimer 0x25      playerCount 0x26

    a permutation of the declared order. Reordering declarations inside a
    struct is a pure LAYOUT change: names and types stay attached to each
    other, so every `p->field` in the body still refers to the same field. That
    is what makes this safe to do mechanically.

    Requires EVERY field to be constrained. A struct with unplaced fields has
    no determined order -- they could belong anywhere -- and guessing where to
    put them would be inventing layout, which is the failure mode this project
    exists to avoid. Declines instead.
    """
    regions = _struct_regions(body)
    if not regions or not mapping:
        return body, False

    out = body
    changed = False
    for start, end in reversed(regions):
        decls = []
        cursor = 0
        for m in DECL.finditer(body, start, end):
            ctype = m.group("type").strip().split()[-1]
            n = int(m.group("count"), 0) if m.group("count") else 1
            unit = 4 if m.group("ptr") else SIZEOF.get(ctype, 0)
            if not unit:
                continue
            cursor = _align(cursor, unit)
            decls.append((m, cursor, unit * n))
            cursor += unit * n
        # A single-field region is skipped deliberately. The mapping is merged
        # across base registers and therefore across STRUCTS, so offset 0
        # carrying a constraint from one struct must not relocate a lone field
        # belonging to a different one -- `u8 menuState;` in RacePlayer would
        # be dragged to 0x24 by a constraint about RaceSplitscreenSelectRowActor.
        # Requiring several mutually-consistent fields is what ties a region to
        # the constraints that actually describe it.
        if len(decls) < 2 or not all(off in mapping for _m, off, _s in decls):
            continue

        placed = sorted(((mapping[off], m, size) for m, off, size in decls),
                        key=lambda t: t[0])
        # overlapping placements describe no struct at all
        pos = 0
        lines = []
        ok = True
        for want, m, size in placed:
            if want < pos:
                ok = False
                break
            if want > pos:
                lines.append(f"    char rpad{pos:02x}[{want - pos:#x}];")
            lines.append("    " + m.group(0).strip())
            pos = want + size
        if not ok:
            continue

        out = out[:start] + "\n" + "\n".join(lines) + "\n" + out[end:]
        changed = True
    return out, changed


def repair(code: str, diff: str) -> tuple[str, bool, dict]:
    """Full pass: read the diff's constraints and apply them. (code, changed, info).

    Declines when the constraints require reordering. Emitting a struct that
    satisfies half a contradictory constraint set is worse than emitting
    nothing: it compiles, scores differently, and hides the real diagnosis.
    """
    mapping, dropped = constraints(diff)
    widths = width_constraints(diff)
    info = {"constraints": len(mapping), "dropped": len(dropped),
            "widths": len(widths),
            "needs_reorder": any(r == "non-monotonic"
                                 for r in dropped.values())}

    # Retype first. A width fix changes a field's SIZE, which moves everything
    # after it, so applying it before any positional repair means the offsets
    # those passes then work from are the corrected ones rather than stale.
    code, retyped = apply_widths(code, widths)
    if retyped:
        info["retyped"] = True
        if not mapping:
            return code, True, info
    if order_violation(mapping):
        info["needs_reorder"] = True
        out, changed = reorder_fields(code, mapping)
        info["reordered"] = changed
        return out, changed or retyped, info
    out, changed = apply_constraints(code, mapping)
    return out, changed or retyped, info


# --------------------------------------------------------------- per object
#
# constraints() pools every offset fault into ONE map that has to be globally
# consistent, and drops whatever disagrees. Measured against the medium tier,
# that is where nearly all of its evidence goes:
#
#   initRacePlayerLandingSnowSpray   base v0 carries TWELVE pairs that all
#       agree on delta +1148 (0x24->0x4a0 ... 0x50->0x4cc) and ONE ambiguous
#       offset, 0x4 -> {0x28,0x2c,0x30}. The whole base is dropped as
#       "contradictory", so twelve unanimous constraints are discarded by one
#       ambiguous one. v0 is the return-value register, so it names a
#       different object after every call -- "base register" is not "object",
#       and it fails worst exactly where the evidence is richest.
#
#   updateCourseSelectCourseDescription   base a2 carries EIGHTEEN pairs with
#       no contradiction at all, and is dropped for "conflicts with another
#       base" because t6 also has an opinion about offset 0. t6 is a loaded
#       pointer and a2 is a parameter; they are unrelated objects that happen
#       to share a number.
#
# Both are the same error: constraints from different objects pooled together.
# The fix is the rule this project already runs on -- propose, do not decide.
# Each internally consistent group becomes its own CANDIDATE, and the oracle
# picks. Nothing here merges, and nothing here is trusted.


def constraint_sets(diff: str) -> list[tuple[str, dict[int, int]]]:
    """Candidate {produced -> expected} maps, one per base, richest first.

    Differs from constraints() in three ways, each measured:

      - an ambiguous produced offset drops ONLY ITSELF, not its whole base
      - bases are never merged, so two objects cannot conflict with each other
      - a set that needs reordering is left out; reorder_fields owns that, and
        a padding pass cannot express it

    A single-pair set is kept. It is weak evidence, but it costs one compile
    and the oracle is the judge, so weak evidence is cheap to test rather than
    dangerous to hold.
    """
    per_base: dict[str, dict[int, set]] = {}
    for base, items in _base_pairs(diff).items():
        for got, want in items:
            per_base.setdefault(base, {}).setdefault(got, set()).add(want)

    sets: list[tuple[str, dict[int, int]]] = []
    for base, seen in per_base.items():
        m = {g: next(iter(w)) for g, w in seen.items() if len(w) == 1}
        if not m or order_violation(m):
            continue
        sets.append((base, m))
    sets.sort(key=lambda bm: -len(bm[1]))
    return sets


def region_fields(code: str, region: tuple[int, int],
                  sizes: dict[str, int] | None = None) -> list:
    """(match, offset, size) for declarations in ONE struct region.

    _fields walks every region with a shared cursor reset per region but a
    SHARED shift in apply_constraints, so a mapping keyed on offset 0 pads the
    first field of every struct in the file at once. Scoping to one region is
    what makes a per-object proposal mean one object.
    """
    if sizes is None:
        sizes = type_sizes(code)
    start, end = region
    cursor = 0
    out = []
    for m in DECL.finditer(code, start, end):
        unit = _unit_of(m, sizes)
        if unit is None:
            # An unresolvable member makes every later offset a guess. Decline
            # the whole region rather than report offsets that are wrong.
            return []
        n = int(m.group("count"), 0) if m.group("count") else 1
        cursor = _align(cursor, unit)
        out.append((m, cursor, unit * n))
        cursor += unit * n
    return out


def apply_constraints_in(code: str, region: tuple[int, int],
                         mapping: dict[int, int]) -> tuple[str, bool]:
    """Pad ONE struct region so its constrained fields land where asked."""
    fields = region_fields(code, region)
    if not fields or not mapping:
        return code, False

    edits: list[tuple[int, str]] = []
    shift = 0
    for m, cursor, _size in fields:
        here = cursor + shift
        want = mapping.get(cursor)
        if want is None or want <= here:
            continue
        gap = want - here
        indent = m.group("indent")
        name = f"dpad{here:02x}"
        if _already_declared(code, name):
            continue
        edits.append((m.start(), f"{indent}char {name}[{gap:#x}];\n"))
        shift += gap

    if not edits:
        return code, False
    out = code
    for start, text in reversed(edits):
        out = out[:start] + text + out[start:]
    return out, True


def _base_pairs(diff: str) -> dict[str, list[tuple[int, int]]]:
    """(produced, expected) offset pairs per base register."""
    per_base: dict[str, list[tuple[int, int]]] = {}
    for a, b in aligned_pairs(diff):
        ma, mb = MEM.match(a), MEM.match(b)
        if not (ma and mb):
            continue
        if ma.group(1) != mb.group(1) or ma.group(4) != mb.group(4):
            continue
        want, got = _num(ma.group(3)), _num(mb.group(3))
        if want is None or got is None or want == got:
            continue
        per_base.setdefault(mb.group(4), []).append((got, want))
    return per_base


def reorder_sets(diff: str) -> list[tuple[str, dict[int, int]]]:
    """Per-base maps that padding CANNOT satisfy, for reorder_fields.

    constraint_sets drops these, which loses the most informative residual in
    the medium tier. updateRacePlayerMode53AerialTrick asks for

        0x78 -> 0x302     0x7c -> 0x2fc

    two adjacent fields whose target order is INVERTED. No amount of padding
    produces that -- padding only moves fields later and preserves order -- so
    the map is not noise, it is a statement that the declared field order is
    wrong. reorder_fields already exists for exactly this and was only ever
    reachable through the global map, which these functions never produce.
    """
    out: list[tuple[str, dict[int, int]]] = []
    for base, items in _base_pairs(diff).items():
        seen: dict[int, set] = {}
        for got, want in items:
            seen.setdefault(got, set()).add(want)
        m = {g: next(iter(w)) for g, w in seen.items() if len(w) == 1}
        if len(m) >= 2 and order_violation(m):
            out.append((base, m))
    out.sort(key=lambda bm: -len(bm[1]))
    return out


def delta_clusters(diff: str) -> list[tuple[str, dict[int, int]]]:
    """Candidate maps grouped by SHIFT, for a base that names several objects.

    A callee-saved register holds a different object in different parts of a
    long function, so its offset pairs pool several structs. Grouping them by
    how far each field has to move separates them again --
    updateRacePlayerMode53AerialTrick's s0 splits into

        +0x14  0x30->0x44, 0x34->0x48        +8  0x6c->0x74, 0x74->0x7c

    each internally consistent, neither reachable while they are pooled.

    This does NOT replace constraint_sets. One struct legitimately shows
    several deltas at once -- a2 on updateCourseSelectCourseDescription is +24
    then +26 because an early field widened -- so clustering alone would tear a
    real struct apart. Both readings are proposed and the oracle picks.

    Negative shifts are dropped: padding cannot move a field earlier, so those
    describe a field that must SHRINK, which is apply_widths' business.
    """
    out: list[tuple[str, dict[int, int]]] = []
    for base, items in _base_pairs(diff).items():
        by_delta: dict[int, dict[int, set]] = {}
        for got, want in items:
            by_delta.setdefault(want - got, {}).setdefault(got, set()).add(want)
        if len(by_delta) < 2:
            continue                   # single delta: constraint_sets has it
        for delta, seen in by_delta.items():
            if delta <= 0:
                continue
            m = {g: next(iter(w)) for g, w in seen.items() if len(w) == 1}
            if len(m) < 2 or order_violation(m):
                continue
            out.append((f"{base}+{delta:#x}", m))
    out.sort(key=lambda bm: -len(bm[1]))
    return out


# ------------------------------------------------------- user-defined sizes
#
# SIZEOF knows the primitives only, and _fields SKIPS a declaration whose type
# it does not know -- without advancing the cursor. A nested struct therefore
# does not just go unconstrained, it silently shifts every field after it:
#
#     /* 0x20 */ Vector3 position;      <- unknown, contributes 0
#     /* 0x2C */ s16     angle;         <- computed at 0x20, actually at 0x2C
#
# On updateRacePlayerMode53AerialTrick the parser reported field offsets
# [0, 32, 48, 68, ...] for a struct whose compiled accesses run to 124, so
# every offset the layout passes compared against was wrong and every
# proposal was silently declined for "not a field offset in that region".
#
# Two rules, both from CLAUDE.md. Resolve what CAN be resolved: a struct
# declared in the same file has a computable size. And where a type is still
# unknown, DECLINE the region rather than continue with a wrong cursor --
# unknown is the default, and a wrong offset is worse than no offset because
# it is unfalsifiable.

STRUCT_NAME = re.compile(r"\}\s*(?P<name>[A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;")


def type_sizes(code: str) -> dict[str, int]:
    """{typedef name -> size in bytes} for structs declared in this source.

    Resolved iteratively so a struct containing another struct works, and
    conservatively: a type that cannot be resolved is simply absent, which
    makes region_fields decline rather than guess.
    """
    regions = []
    for start, end in _struct_regions(code):
        m = STRUCT_NAME.match(code, end)
        if m:
            regions.append((m.group("name"), start, end))

    sizes: dict[str, int] = {}
    for _pass in range(4):                 # enough for realistic nesting
        progressed = False
        for name, start, end in regions:
            if name in sizes:
                continue
            size = _region_size(code, start, end, sizes)
            if size is not None:
                sizes[name] = size
                progressed = True
        if not progressed:
            break
    return sizes


def _unit_of(m, sizes: dict[str, int]) -> int | None:
    """Size of one element of this declaration, or None if unknown."""
    if m.group("ptr"):
        return 4
    ctype = m.group("type").strip().split()[-1]
    if ctype in SIZEOF:
        return SIZEOF[ctype]
    return sizes.get(ctype)


def _region_size(code: str, start: int, end: int,
                 sizes: dict[str, int]) -> int | None:
    """Total size of one struct body, or None while a member is unresolved."""
    cursor, maxalign = 0, 1
    for m in DECL.finditer(code, start, end):
        unit = _unit_of(m, sizes)
        if unit is None:
            return None
        n = int(m.group("count"), 0) if m.group("count") else 1
        cursor = _align(cursor, unit)
        cursor += unit * n
        maxalign = max(maxalign, min(unit, 4))
    if cursor == 0:
        return None
    return (cursor + maxalign - 1) // maxalign * maxalign

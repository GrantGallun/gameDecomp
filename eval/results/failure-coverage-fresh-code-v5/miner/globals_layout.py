"""Reconstruct global data objects from every function that touches them.

The miner records each global access under its own resolved address, so
`global:0x80121B50`, `global:0x80121B55` and `global:0x80121B56` are three
separate bases holding one offset each. They are three FIELDS OF ONE OBJECT,
and solver/structgen.py::layout excludes `global:%` outright, so the entire
program-wide picture of global data is currently unused.

The cost of that is concrete. On the near-miss set, a target function sees 62
field offsets on the globals it touches; the rest of the program sees 166 --
2.7x more -- and some individual fields are observed by 161 different
functions. A model reading one function's assembly cannot possibly derive that,
which is precisely the kind of knowledge the KB is supposed to supply.

WHY THIS IS EVIDENCE, NOT INFERENCE
    An address is what the binary encodes. Two accesses to 0x80121B55 touch the
    same byte, in every function, with no type reasoning involved -- unlike
    `param0`, whose identity differs per function and cannot be linked without
    inferring types. So aggregating by address is mechanical.

    NAMING an object would be inference and is deliberately not done here.
    Grouping is also a judgement -- see the gap caveat below -- so the grouping
    is reported with its evidence and never asserted as a fact about the
    program.

CAVEATS, because this is a hypothesis about layout and must not be dressed up
as a measurement:
  - The cluster gap is a heuristic. Two objects closer together than the gap
    merge into one; a sparsely-accessed object splits. `min_funcs` and the gap
    are both tunable and neither is derived from anything.
  - A cluster's base is the LOWEST OBSERVED address, which is the start of the
    object only if the program ever touches its first field. Offsets are
    therefore relative to first-observed, not to the true object start, and are
    labelled that way.
  - An unobserved byte is unknown, never assumed to be padding of any width.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

GLOBAL_ACCESSES = """
SELECT base, offset, width, signed, func_addr
  FROM evidence
 WHERE kind = 'mem_access'
   AND base LIKE 'global:%'
   AND width IN (1, 2, 4, 8)
"""

WIDTH_TYPE = {1: ("s8", "u8"), 2: ("s16", "u16"),
              4: ("s32", "u32"), 8: ("s64", "u64")}


@dataclass
class Field:
    offset: int                       # relative to the cluster base
    addr: int                         # absolute, the thing actually observed
    width: int
    signed_votes: int = 0
    unsigned_votes: int = 0
    funcs: set = field(default_factory=set)

    @property
    def ctype(self) -> str:
        s, u = WIDTH_TYPE.get(self.width, ("s32", "u32"))
        return u if self.unsigned_votes > self.signed_votes else s


@dataclass
class GlobalObject:
    base: int
    fields: list          # list[Field], ascending by offset

    @property
    def span(self) -> int:
        return (self.fields[-1].addr - self.base + self.fields[-1].width
                if self.fields else 0)

    @property
    def func_count(self) -> int:
        return len({f for fl in self.fields for f in fl.funcs})


def collect(conn: sqlite3.Connection) -> dict[int, Field]:
    """Absolute address -> Field, merging every access to that address.

    The widest access wins, exactly as structgen does for parameters: a
    narrower field cannot hold a wider one, and one location read at two
    widths is ordinary C rather than a contradiction.
    """
    acc: dict[int, Field] = {}
    for base, off, width, signed, func_addr in conn.execute(GLOBAL_ACCESSES):
        try:
            addr = int(str(base).split(":", 1)[1], 16) + (off or 0)
        except (IndexError, ValueError):
            continue
        f = acc.get(addr)
        if f is None:
            f = acc[addr] = Field(offset=0, addr=addr, width=width)
        if width > f.width:
            f.width = width
        if signed:
            f.signed_votes += 1
        else:
            f.unsigned_votes += 1
        f.funcs.add(func_addr)
    return acc


def cluster(acc: dict[int, Field], gap: int = 0x40,
            min_fields: int = 2, min_funcs: int = 2) -> list[GlobalObject]:
    """Group addresses into objects. The gap is a heuristic, not a finding."""
    out: list[GlobalObject] = []
    cur: list[Field] = []
    for addr in sorted(acc):
        f = acc[addr]
        if cur and addr - cur[-1].addr > gap:
            out.append(GlobalObject(cur[0].addr, cur))
            cur = []
        cur.append(f)
    if cur:
        out.append(GlobalObject(cur[0].addr, cur))

    kept = []
    for obj in out:
        for f in obj.fields:
            f.offset = f.addr - obj.base
        if len(obj.fields) >= min_fields and obj.func_count >= min_funcs:
            kept.append(obj)
    return kept


def objects(conn: sqlite3.Connection, **kw) -> list[GlobalObject]:
    return cluster(collect(conn), **kw)


def for_address(objs: list[GlobalObject], addr: int) -> GlobalObject | None:
    """The object containing an address, if any."""
    for o in objs:
        if o.base <= addr <= o.base + o.span:
            return o
    return None


def render(obj: GlobalObject, name: str = "") -> str:
    """A C struct for one object, with provenance on every field.

    Offsets are relative to the LOWEST OBSERVED address, which the header says
    plainly: if the program never touches the object's first field, the true
    object starts earlier and every offset here is shifted. Saying so is the
    difference between a layout and a guess presented as one.
    """
    label = name or f"obj_{obj.base:08X}"
    lines = [f"/* {label} @ {obj.base:#010x} -- offsets are relative to the",
             f"   LOWEST OBSERVED address, not necessarily the object start.",
             f"   Derived from {obj.func_count} functions' accesses. */",
             f"struct {label} {{"]
    cursor = 0
    for f in obj.fields:
        if f.offset > cursor:
            lines.append(f"    char unk_{cursor:02x}[{f.offset - cursor:#x}];"
                         f"   /* never observed */")
        elif f.offset < cursor:
            continue                       # overlapping access, widest already kept
        lines.append(f"    {f.ctype} f_{f.offset:x};"
                     f"   /* {f.addr:#010x}, {len(f.funcs)} functions */")
        cursor = f.offset + f.width
    lines.append("};")
    return "\n".join(lines)

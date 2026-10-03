"""Grade front-end output against a finished decomp. Reference side ONLY.

Nothing a stage produces may be computed from this module's output. It reads
the reference build (ELF, link map, objects, source) to say how right a stage
was, and buckets every disagreement so that each one is explained or is a bug
(CLAUDE.md "explained-or-broken").

Reference function truth is assembled from two places, because the linked ELF
alone is incomplete for IDO:

* ELF STT_FUNC symbols with sizes: every global function.
* Static functions. Nothing of a `static` function reaches the linked ELF
  (SBK1: 0 local function symbols), and the object symtab is no help either:
  IDO writes a sized *UND* entry only for some statics (one with a forward
  declaration) and nothing at all for others (_MakeMotorData, _Putfld).
  Source order cannot place them: libultra's env.c is laid out in exactly
  REVERSE source order (`object_order` counts this per object). So statics
  come from address-side facts: code in an object's .text that no global
  covers is an interval, and each interval begins with a static -- an exact
  start. When intervals and source-defined statics are equally many, each
  interval is one static and its size is exact too, unless the interval runs
  into the object's zero-padded tail. Everything else is UNCHECKABLE and is
  reported as such, never counted as agreement; an interval with no source
  static to explain it is reported as unexplained.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

OBJDUMP = "mips-linux-gnu-objdump"


@dataclass(frozen=True)
class RefFunc:
    addr: int
    name: str
    size: int | None     # None: size uncheckable (static not alone in its gap)
    kind: str            # "global" | "static"


@dataclass
class Reference:
    funcs: dict[int, RefFunc]
    # statics whose start cannot be derived: (object, name)
    uncheckable_statics: list[tuple[str, str]] = field(default_factory=list)
    objects: list[tuple[str, int, int]] = field(default_factory=list)
    # uncovered code in an object with no source static to explain it
    unexplained_intervals: list[tuple[str, int, int]] = field(default_factory=list)
    # objects whose global functions sit in forward/reverse/mixed source order
    object_order: dict[str, int] = field(default_factory=dict)
    padding_intervals: list[tuple[str, int, int]] = field(default_factory=list)
    # (object, its static intervals, statics with no derivable start)
    shared_static_intervals: list[tuple[str, list[tuple[int, int]], int]] =         field(default_factory=list)


def _symtab(path: Path) -> list[list[str]]:
    out = subprocess.run([OBJDUMP, "-t", str(path)], capture_output=True,
                         text=True, check=True).stdout
    return [line.split() for line in out.splitlines()]


def elf_functions(elf: Path) -> dict[int, RefFunc]:
    funcs: dict[int, RefFunc] = {}
    for t in _symtab(elf):
        if len(t) >= 6 and "F" in t[1:-3] and t[-3].startswith("."):
            addr, size, name = int(t[0], 16), int(t[-2], 16), t[-1]
            if size and addr not in funcs:
                funcs[addr] = RefFunc(addr, name, size, "global")
    return funcs


TEXT_RE = re.compile(r"^ \.text\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+(\S+\.o)$")


def map_text_objects(map_path: Path) -> list[tuple[str, int, int]]:
    objs = []
    for line in map_path.read_text(errors="replace").splitlines():
        m = TEXT_RE.match(line)
        if m and int(m.group(2), 16):
            objs.append((m.group(3), int(m.group(1), 16), int(m.group(2), 16)))
    return objs


DEF_RE = re.compile(r"^(?![ \t#])[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\([^;{}]*?\)\s*\{",
                    re.M)
NOT_FUNCS = {"if", "while", "for", "switch", "return", "sizeof"}


def source_definitions(src: Path) -> list[str]:
    """Function names defined in a C file, in definition order."""
    text = src.read_text(errors="replace")
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), text,
                  flags=re.S)
    return [m.group(1) for m in DEF_RE.finditer(text)
            if m.group(1) not in NOT_FUNCS]


def reference(repo: Path) -> Reference:
    repo = Path(repo)
    elf = next(repo.glob("build/*.elf"))
    map_path = next(repo.glob("build/*.map"))
    funcs = elf_functions(elf)
    ref = Reference(funcs=dict(funcs), objects=map_text_objects(map_path))

    word_at = elf_word_reader(elf)

    for obj_rel, base, size in ref.objects:
        end = base + size
        inside = sorted((f for f in funcs.values() if base <= f.addr < end),
                        key=lambda f: f.addr)
        src = (repo / re.sub(r"^build/", "", obj_rel)).with_suffix(".c")
        defs = source_definitions(src) if src.exists() else []
        names = {f.name for f in inside}
        statics = [n for n in defs if n not in names]

        # Address order vs source order, as a measured fact per object.
        ranked = [defs.index(f.name) for f in inside if f.name in defs]
        if len(ranked) >= 2:
            order = ("forward" if ranked == sorted(ranked) else
                     "reverse" if ranked == sorted(ranked, reverse=True) else "mixed")
            ref.object_order[order] = ref.object_order.get(order, 0) + 1

        # Code in the object that no global covers.
        cursor, intervals = base, []
        for f in inside:
            if f.addr > cursor:
                intervals.append((cursor, f.addr))
            cursor = max(cursor, f.addr + f.size)
        if cursor < end:
            intervals.append((cursor, end))
        # All-zero intervals are padding, not functions (the object's trailing
        # alignment; the entry segment's fill after a handwritten entrypoint).
        zero = [(a, b) for a, b in intervals
                if all(word_at(x) == 0 for x in range(a, b, 4))]
        ref.padding_intervals += [(obj_rel, a, b) for a, b in zero]
        intervals = [iv for iv in intervals if iv not in zero]
        if not intervals:
            continue
        if not statics:
            ref.unexplained_intervals += [(obj_rel, a, b) for a, b in intervals]
            continue
        one_each = len(intervals) == len(statics)
        for i, (a, b) in enumerate(intervals):
            at_tail = b == end and word_at(b - 4) == 0
            sized = one_each and not at_tail
            ref.funcs.setdefault(a, RefFunc(
                a, statics[i] if one_each else f"static@{a:#x}",
                b - a if sized else None, "static"))
        if len(intervals) < len(statics):
            ref.uncheckable_statics += [(obj_rel, f"{len(statics) - len(intervals)}"
                                         " statics share an interval")]
            ref.shared_static_intervals.append(
                (obj_rel, intervals, len(statics) - len(intervals)))
        elif len(intervals) > len(statics):
            ref.unexplained_intervals += [(obj_rel, a, b)
                                          for a, b in intervals[len(statics):]]
    return ref


def elf_word_reader(elf: Path):
    """vram -> big-endian word from the reference ELF's PROGBITS sections."""
    import struct
    data = Path(elf).read_bytes()
    shoff, = struct.unpack_from(">I", data, 0x20)
    shentsize, shnum = struct.unpack_from(">HH", data, 0x2E)
    sections = []
    for i in range(shnum):
        _, typ, _, addr, off, size = struct.unpack_from(
            ">IIIIII", data, shoff + i * shentsize)
        if typ == 1 and addr and size:          # SHT_PROGBITS
            sections.append((addr, off, size))

    def word_at(vram: int) -> int:
        for addr, off, size in sections:
            if addr <= vram < addr + size:
                return int.from_bytes(data[off + vram - addr: off + vram - addr + 4], "big")
        raise KeyError(f"{vram:#x} not in a PROGBITS section")
    return word_at


# ------------------------------------------------------------------ compare


def compare(ours: dict[int, int], reference: Reference, lo: int, hi: int,
            word_at, known: dict[int, str] | None = None) -> dict:
    """Bucket every function in [lo, hi). `ours` maps start -> size in bytes.

    `word_at(vram)` reads the ROM word, used only to describe a disagreement
    (e.g. that a size difference is all nops), never to decide our answer.
    `known` maps an address to the written reason it is whitelisted; a
    disagreement at that address moves to `whitelisted` with its reason, and
    a whitelist entry that no longer matches anything is reported as `stale`.
    """
    known = dict(known or {})
    ref = {a: f for a, f in reference.funcs.items() if lo <= a < hi}
    ours = {a: s for a, s in ours.items() if lo <= a < hi}
    b: dict[str, list] = {k: [] for k in (
        "exact", "start_only_uncheckable_size", "uncheckable_static_start",
        "size_padding", "size_other", "missed_start", "extra_start",
        "whitelisted")}
    for a, f in sorted(ref.items()):
        if a not in ours:
            b["missed_start"].append({"addr": a, "name": f.name, "size": f.size,
                                      "kind": f.kind})
        elif f.size is None:
            b["start_only_uncheckable_size"].append(a)
        elif ours[a] == f.size:
            b["exact"].append(a)
        else:
            short, long_ = sorted((ours[a], f.size))
            tail = [word_at(x) for x in range(a + short, a + long_, 4)]
            key = "size_padding" if all(w == 0 for w in tail) else "size_other"
            b[key].append({"addr": a, "name": f.name, "ref": f.size,
                           "ours": ours[a], "kind": f.kind})
    starts = sorted(ref)
    budget = {obj: n for obj, _, n in reference.shared_static_intervals}
    for a in sorted(set(ours) - set(ref)):
        shared = next((obj for obj, ivs, _ in reference.shared_static_intervals
                       if any(x < a < y for x, y in ivs)), None)
        if shared is not None and budget[shared] > 0:
            budget[shared] -= 1
            b["uncheckable_static_start"].append({"addr": a, "object": shared})
            continue
        prev = max((s for s in starts if s <= a), default=None)
        within = (prev is not None and ref[prev].size is not None
                  and a < prev + ref[prev].size)
        b["extra_start"].append({"addr": a, "size": ours[a],
                                 "inside": ref[prev].name if within else None})
    for key in ("size_other", "size_padding", "missed_start", "extra_start"):
        keep = []
        for item in b[key]:
            reason = known.pop(item["addr"], None)
            if reason is None:
                keep.append(item)
            else:
                b["whitelisted"].append({**item, "bucket": key, "reason": reason})
        b[key] = keep
    summary = {k: len(v) for k, v in b.items()}
    summary["reference"] = len(ref)
    summary["ours"] = len(ours)
    summary["stale_whitelist"] = len(known)
    return {"summary": summary, "buckets": b, "stale_whitelist": sorted(known)}


KNOWN = Path(__file__).with_name("known_disagreements.json")


def load_known(rom_sha1: str) -> dict[int, str]:
    """Whitelisted function disagreements for one ROM: address -> reason."""
    import json
    if not KNOWN.exists():
        return {}
    entries = json.loads(KNOWN.read_text(encoding="utf-8")).get(rom_sha1, {})
    return {int(a, 16): e["reason"] for a, e in entries.get("functions", {}).items()}

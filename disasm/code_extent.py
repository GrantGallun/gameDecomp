"""Stage 2: where CPU code ends inside a segment, from the ROM alone.

The linker lays a segment out as .text then .data/.rodata, so the question is
one boundary, bracketed from both sides by observation:

* Code reaches it from below. Starting at the entry points the entry code
  names, follow `jal` targets and address-taken pointers (lui/addiu pairs)
  whose targets decode as a complete function. The highest end of any reached
  function, aligned up over zero words, is the end of CPU text.
* Data reaches it from above. Every lui-paired load or store into the segment
  is an access to data; the lowest such address is where data starts.

What lies between the two is not CPU code, and is not yet resolved: RSP
microcode (entered only through pointers handed to the RSP) and data reached
only through pointers both live there. SBK1 has only microcode in it; SBK2
also has 0x1638 bytes of pointer-only .data, so the lowest direct access is
a BOUND on where data starts, not the start. The region is reported as
`non_cpu_or_data` with its address-taken entry points, never folded into
either side.

An address-taken target is admitted as code only below the data bound (a
pointer to data is the common case) and only if it walks as a function. The
bound can only fall as code is discovered, so admission is re-checked at the
end and a violation is an error, never a silent drop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import rabbitizer

from disasm.rom import Segment


@dataclass
class Walk:
    start: int
    end: int | None                  # exclusive vram; None = not a function
    calls: set[int] = field(default_factory=set)
    addr_refs: set[int] = field(default_factory=set)
    mem_refs: set[int] = field(default_factory=set)


@dataclass
class CodeExtent:
    segment: Segment
    text_end: int                    # vram, exclusive
    data_bound: int | None           # vram of the lowest CPU data access: data
                                     # begins AT OR BEFORE here (SBK1: at; SBK2:
                                     # 0x1638 bytes before, pointer-only data)
    functions: dict[int, int]        # start -> end, every function reached
    via_call: set[int]
    via_pointer: set[int]
    non_cpu_entries: list[int]       # address-taken, between text and data
    rejected_pointers: int           # address refs into text that do not walk

    def rom(self, vram: int) -> int:
        return self.segment.rom_start + (vram - self.segment.vram)

    def summary(self) -> dict:
        h = lambda v: None if v is None else f"{v:#010x}"
        return {
            "text_end_vram": h(self.text_end),
            "text_end_rom": h(self.rom(self.text_end)),
            "data_bound_vram": h(self.data_bound),
            "data_bound_rom": h(self.rom(self.data_bound)) if self.data_bound else None,
            "functions_reached": len(self.functions),
            "via_call": len(self.via_call),
            "via_pointer": len(self.via_pointer),
            "non_cpu_entries_rom": [h(self.rom(v)) for v in self.non_cpu_entries],
            "rejected_pointers": self.rejected_pointers,
        }


def _reg(r) -> str:
    return str(r).split(":")[-1].strip(" >").split()[0]


def _dest(d) -> str | None:
    try:
        if d.modifiesRt():
            return _reg(d.rt)
        if d.modifiesRd():
            return _reg(d.rd)
    except RuntimeError:
        return None
    return None


class Decoder:
    def __init__(self, rom: bytes, seg: Segment):
        self.seg = seg
        self.n = (seg.rom_end - seg.rom_start) // 4
        self.words = [int.from_bytes(rom[o:o + 4], "big")
                      for o in range(seg.rom_start, seg.rom_end, 4)]
        self._cache: dict[int, rabbitizer.Instruction] = {}

    def contains(self, vram: int) -> bool:
        return self.seg.vram <= vram < self.seg.vram + 4 * self.n and vram % 4 == 0

    def word(self, vram: int) -> int:
        return self.words[(vram - self.seg.vram) // 4]

    def insn(self, vram: int) -> rabbitizer.Instruction:
        d = self._cache.get(vram)
        if d is None:
            d = rabbitizer.Instruction(self.word(vram), vram=vram)
            self._cache[vram] = d
        return d

    def walk(self, start: int, relative_only: bool = False) -> Walk:
        """Linear body to a return or eret past every forward branch.

        An invalid instruction before that end means `start` is not a
        function, and the walk says so (end=None) rather than guessing.
        `relative_only` ignores absolute `j` targets: in code whose load
        address is not yet known they point nowhere meaningful.
        """
        w = Walk(start, None)
        far = start
        hi: dict[str, int] = {}
        v = start
        while self.contains(v):
            d = self.insn(v)
            if not d.isValid():
                return w
            if d.isFunctionCall() and d.isJumpWithAddress():
                w.calls.add(d.getInstrIndexAsVram())
            elif d.isBranch() or (d.isJump() and d.isJumpWithAddress()
                                  and not relative_only):
                try:
                    far = max(far, d.getBranchVramGeneric())
                except (RuntimeError, ValueError):
                    pass
            op = d.getOpcodeName()
            if op == "lui":
                hi[_reg(d.rt)] = (d.getProcessedImmediate() & 0xFFFF) << 16
            elif op == "addiu" and _reg(d.rs) in hi:
                w.addr_refs.add((hi[_reg(d.rs)] + d.getProcessedImmediate()) & 0xFFFFFFFF)
            elif d.doesDereference() and _reg(d.rs) in hi:
                w.mem_refs.add((hi[_reg(d.rs)] + d.getProcessedImmediate()) & 0xFFFFFFFF)
            dest = _dest(d)
            if dest and op != "lui":
                hi.pop(dest, None)
            if v >= far and (d.isReturn() or op == "eret"):
                w.end = v + (4 if op == "eret" else 8)
                return w
            v += 4
        return w


def find(rom: bytes, seg: Segment, entries: list[int]) -> CodeExtent:
    dec = Decoder(rom, seg)
    walks: dict[int, Walk] = {}
    via_call, via_pointer = set(), set()
    pending_ptrs: set[int] = set()
    rejected = 0

    def reach(work: list[int]):
        while work:
            f = work.pop()
            if f in walks or not dec.contains(f):
                continue
            w = walks[f] = dec.walk(f)
            if w.end is None:
                continue
            work.extend(w.calls)
            via_call.update(c for c in w.calls if dec.contains(c))
            pending_ptrs.update(r for r in w.addr_refs if dec.contains(r))

    def data_bound() -> int | None:
        refs = [m for w in walks.values() if w.end for m in w.mem_refs if dec.contains(m)]
        return min(refs) if refs else None

    for e in entries:
        if not dec.contains(e):
            raise ValueError(f"entry {e:#x} outside segment")
    reach(list(entries))
    tried: set[int] = set()
    while True:
        bound = data_bound()
        new = [p for p in pending_ptrs - tried - set(walks)
               if bound is None or p < bound]
        if not new:
            break
        tried.update(new)
        admitted = []
        for p in new:
            w = dec.walk(p)
            if w.end is not None and (bound is None or w.end <= bound):
                admitted.append(p)
            else:
                rejected += 1
        via_pointer.update(admitted)
        reach(admitted)

    funcs = {f: w.end for f, w in walks.items() if w.end is not None}
    bad = [f for f in entries if f not in funcs]
    if bad:
        raise ValueError(f"entry points do not walk as functions: {[hex(b) for b in bad]}")
    bound = data_bound()
    over = [f for f, e in funcs.items() if bound is not None and e > bound]
    if over:
        raise ValueError(f"{len(over)} reached functions run past the data bound "
                         f"{bound:#x}: {[hex(f) for f in sorted(over)[:5]]}")
    end = max(funcs.values())
    # Align over zero words only: alignment padding, never code.
    limit = bound if bound is not None else seg.vram + 4 * dec.n
    while end % 16 and end < limit and dec.word(end) == 0:
        end += 4
    non_cpu = sorted(p for p in pending_ptrs
                     if end <= p and (bound is None or p < bound))
    return CodeExtent(seg, end, bound, funcs, via_call & set(funcs),
                      via_pointer & set(funcs), non_cpu, rejected)

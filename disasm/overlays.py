"""Stage 1b: code overlays, from the ROM alone.

A game DMAs an overlay from a ROM range it carries somewhere, and carries
the two ends together: as consecutive words of a table in .data, or as two
address constants built in the same loader function. So:

1. Ranges. Every such pair (a, b), a < b, outside the boot image is a
   candidate range. Single offsets are NOT used: aligned sizes and asset
   offsets made spurious cuts inside overlays (SBK2: 0xA0000, 0xB0000).
2. Code test. Code must start the range: the sequential split (below) finds
   two consecutive functions, or one that opens with a stack-frame prologue.
   Asset ranges (compressed data, display lists) fail this.
3. Sequential split. Walking from the first non-zero word with relative
   branches only -- an absolute `j` target means nothing before the load
   address is known -- yields every function start, leaves included, and
   where the code ends (`text_size`).
4. Load address. Position-dependent code says where it runs. Calls: every
   `jal` target the overlay makes into its own image must land on one of its
   function starts; each (target, start) pair proposes V = T - offset.
   Self-references: its lui/addiu constants into its own .data/.rodata must
   land inside the image past the code. A KSEG0 word in the same table row
   as the ROM range proposes a candidate but casts no vote. Targets inside
   the boot image are calls into main and are excluded (counting them put
   three SBK2 levels inside main). The winner needs MIN_VOTES and a strict
   lead; otherwise the load address is UNKNOWN and the overlay is reported,
   not placed.

Overlays that share a load address (SBK2's sixteen levels at 0x800BB2B0) are
voted on independently. A placed overlay goes through the same code_extent
and functions stages as the boot segment.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from disasm import code_extent
from disasm.code_extent import CodeExtent
from disasm.rom import Segment

MIN_VOTES = 2
KSEG0 = (0x80000000, 0xA0000000)
JR_RA = 0x03E00008
PROBE_VRAM = 0x80000000


@dataclass
class Overlay:
    rom_start: int
    rom_end: int
    code_start: int
    text_size: int
    starts: list[int]                 # function offsets from code_start
    vram: int | None                  # of rom_start
    votes: int
    runner_up: int
    sources: list[str] = field(default_factory=list)
    alternatives: list[int] = field(default_factory=list)   # other ends seen
    vote_detail: dict = field(default_factory=dict)

    def segment(self, name: str) -> Segment:
        return Segment(name, self.rom_start, self.rom_end, self.vram,
                       derived_from=f"overlay {'+'.join(self.sources)}; load "
                                    f"address by {self.votes} jal votes "
                                    f"(runner-up {self.runner_up})")


def _word(rom: bytes, o: int) -> int:
    return int.from_bytes(rom[o:o + 4], "big")


def _is_prologue(word: int) -> bool:
    return (word >> 16) == 0x27BD and (word & 0x8000) != 0     # addiu sp,sp,-N


def boot_walks(rom: bytes, boot: Segment, boot_ext: CodeExtent) -> dict:
    dec = code_extent.Decoder(rom, boot)
    return {f: dec.walk(f) for f in boot_ext.functions}


def range_pairs(rom: bytes, boot: Segment, boot_ext: CodeExtent,
                walks: dict | None = None) -> dict[tuple[int, int], set[str]]:
    size = len(rom)

    def ok(x: int) -> bool:
        return boot.rom_end <= x <= size and x % 4 == 0

    out: dict[tuple[int, int], set[str]] = collections.defaultdict(set)
    for o in range(boot_ext.rom(boot_ext.text_end), boot.rom_end - 4, 4):
        a, b = _word(rom, o), _word(rom, o + 4)
        if ok(a) and ok(b) and a < b:
            out[(a, b)].add("data_pair")
    walks = walks or boot_walks(rom, boot, boot_ext)
    for w in walks.values():
        consts = sorted(r for r in w.addr_refs if ok(r))
        for a, b in zip(consts, consts[1:]):
            out[(a, b)].add("code_pair")
    return out


def split(rom: bytes, start: int, end: int) -> tuple[int, list[int], int] | None:
    """(code_start, function offsets, text_size) by sequential walk, or None."""
    o = start
    while o < end and _word(rom, o) == 0:
        o += 4
    if end - o < 8:
        return None
    seg = Segment("probe", o, end, PROBE_VRAM)
    dec = code_extent.Decoder(rom, seg)
    starts, v = [], PROBE_VRAM
    limit = PROBE_VRAM + (end - o)
    while v < limit:
        w = dec.walk(v, relative_only=True)
        if w.end is None:
            break
        starts.append(v - PROBE_VRAM)
        v = w.end
        while v < limit and dec.word(v) == 0:
            v += 4
    if not starts:
        return None
    # Data that happens to decode to one return is plausible; two consecutive
    # functions, or a frame prologue at the very start, is code. (A 64-byte
    # "prologue or return" window rejected SBK2's credits overlay, which opens
    # with a long leaf.)
    head = [_word(rom, x) for x in range(o, min(end, o + 16), 4)]
    if len(starts) < 2 and not any(_is_prologue(x) for x in head):
        return None
    last = starts[-1] + PROBE_VRAM
    text_end = dec.walk(last, relative_only=True).end - PROBE_VRAM
    return o, starts, text_end


def code_facts(rom: bytes, code_start: int, starts: list[int], text_size: int
               ) -> tuple[set[int], set[int]]:
    """(jal targets, absolute address constants) made by the overlay's code."""
    seg = Segment("probe", code_start, code_start + text_size, PROBE_VRAM)
    dec = code_extent.Decoder(rom, seg)
    jals, consts = set(), set()
    for s in starts:
        w = dec.walk(PROBE_VRAM + s, relative_only=True)
        jals |= {t for t in w.calls if KSEG0[0] <= t < KSEG0[1]}
        consts |= {c for c in (w.addr_refs | w.mem_refs) if KSEG0[0] <= c < KSEG0[1]}
    return jals, consts


def vote(rom: bytes, code_start: int, starts: list[int], text_size: int,
         image_size: int, boot: Segment, table_candidates: set[int] = frozenset(),
         external: set[int] = frozenset(), loader_candidates: set[int] = frozenset()
         ) -> tuple[int | None, int, int, dict]:
    """(vram of code_start, score, runner-up score, score detail).

    Two independent votes, both position-dependent facts of the overlay's
    own code:
    * calls: jal targets landing on one of its function starts;
    * self-references: lui/addiu or lui/load constants landing inside its own
      image past the code (its .data/.rodata).
    Targets inside the boot image are calls INTO main, never evidence, and a
    candidate image may not overlap the boot image.
    """
    jals, consts = code_facts(rom, code_start, starts, text_size)
    jals = {t for t in jals if not boot.vram <= t < boot.vram_end}
    consts = {c for c in consts if not boot.vram <= c < boot.vram_end}
    start_set = set(starts)
    proposals: collections.Counter = collections.Counter()
    for t in jals:
        for s in starts:
            proposals[t - s] += 1
    for t in external:
        for s in starts:
            proposals[t - s] += 1
    for v in table_candidates | loader_candidates:
        proposals[v] += 0

    def ok(v: int) -> bool:
        return (KSEG0[0] <= v and (v + image_size <= boot.vram or v >= boot.vram_end))

    def detail(v: int) -> dict:
        in_text = lambda x: v <= x < v + text_size
        return {
            "calls": sum(1 for t in jals if in_text(t) and (t - v) in start_set),
            "func_ptrs": sum(1 for c in consts if in_text(c) and (c - v) in start_set),
            "self_refs": sum(1 for c in consts if v + text_size <= c < v + image_size),
            # Calls from the boot image support but never contradict: siblings
            # sharing a load address each receive the others' calls.
            "boot_calls": sum(1 for t in external if in_text(t) and (t - v) in start_set),
            # A wrong V lands some of the overlay's own calls mid-function.
            "contradictions": sum(1 for t in jals if in_text(t) and (t - v) not in start_set),
            # Named by the loader function or the table row: independent evidence.
            "named": int(v in named),
        }

    named = table_candidates | loader_candidates

    def score(d: dict) -> int:
        return (d["calls"] + d["func_ptrs"] + d["self_refs"] + d["boot_calls"]
                + d["named"] - 3 * d["contradictions"])

    cands = [v for v, _ in proposals.most_common(64) if ok(v)] +             [v for v in table_candidates | loader_candidates if ok(v)]
    if not cands:
        return None, 0, 0, {}
    ranked = sorted({v: score(detail(v)) for v in cands}.items(),
                    key=lambda kv: -kv[1])
    best, s1 = ranked[0]
    s2 = ranked[1][1] if len(ranked) > 1 else 0
    d = detail(best)
    if s1 < MIN_VOTES or s1 == s2 or d["contradictions"]:
        return None, s1, s2, d
    return best, s1, s2, d


def table_vrams(rom: bytes, boot: Segment, boot_ext: CodeExtent, a: int, b: int) -> set[int]:
    """KSEG0 words within three words of a data-table row holding (a, b)."""
    out = set()
    for o in range(boot_ext.rom(boot_ext.text_end), boot.rom_end - 4, 4):
        if _word(rom, o) == a and _word(rom, o + 4) == b:
            for x in range(max(boot.rom_start, o - 12), min(boot.rom_end, o + 20), 4):
                w = _word(rom, x)
                if KSEG0[0] <= w < KSEG0[1] and w % 16 == 0:
                    out.add(w)
    return out


def loader_vrams(walks: dict, boot: Segment, a: int, b: int) -> set[int]:
    """KSEG0 constants built in the same boot function as the range (a, b)."""
    out = set()
    for w in walks.values():
        refs = w.addr_refs
        if a in refs and b in refs:
            out |= {r for r in refs if KSEG0[0] <= r < KSEG0[1] and r % 16 == 0
                    and not boot.vram <= r < boot.vram_end}
    return out


def find(rom: bytes, boot: Segment, boot_ext: CodeExtent) -> list[Overlay]:
    walks = boot_walks(rom, boot, boot_ext)
    pairs = range_pairs(rom, boot, boot_ext, walks)
    external = {t for w in walks.values() for t in w.calls
                if KSEG0[0] <= t < KSEG0[1] and not boot.vram <= t < boot.vram_end}
    by_start: dict[int, list[int]] = collections.defaultdict(list)
    for a, b in pairs:
        by_start[a].append(b)
    out = []
    for a in sorted(by_start):
        ends = sorted(by_start[a])
        res = split(rom, a, ends[-1])
        if res is None:
            continue
        code_start, starts, text_size = res
        # The overlay's image must hold its code: take the smallest end that does.
        fits = [b for b in ends if b >= code_start + text_size]
        if not fits:
            continue
        b = fits[0]
        lead = code_start - a
        tv = {t + lead for t in table_vrams(rom, boot, boot_ext, a, b)}
        lv = {t + lead for t in loader_vrams(walks, boot, a, b)}
        v, s1, s2, why = vote(rom, code_start, starts, text_size, b - code_start,
                              boot, tv, external, lv)
        vram = None if v is None else v - lead
        out.append(Overlay(a, b, code_start, text_size, starts, vram, s1, s2,
                           sorted(pairs[(a, b)]), [e for e in ends if e != b], why))
    # Overlays do not overlap in ROM. A candidate starting inside another's
    # code is a pair that happens to begin mid-stream (SBK2: 0xA0000 in race).
    inside = lambda o, p: p is not o and p.code_start <= o.rom_start < p.code_start + p.text_size
    return [o for o in out if not any(inside(o, p) for p in out)]

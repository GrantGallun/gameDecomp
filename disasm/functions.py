"""Stage 3: function boundaries inside CPU text.

spimdisasm does the splitting (handoff idea 1: do not write a disassembler).
Two things are ours:

* Seeds. Every function start stage 2 proved -- jal targets and address-taken
  pointers that walk as functions -- is registered in spimdisasm's context
  before analysis, so a function entered only by pointer (an exception
  handler, a thread entry) is split even when no `jal` names it.
* Padding. Zero words after a function's last control transfer and its delay
  slot are alignment padding, not part of the function: the compiler's
  `.size` excludes them and a reassembled function must too (bootThreadMain
  was one padding word away from exact). spimdisasm folds them into the
  preceding function; `split_padding` cuts them off and records them. The
  same padding also marks a boundary when nothing else does
  (`padding_boundaries`): osPfsIsPlug is never called, and only the zeros
  after __osCleanupThread's no-return jal say where it begins.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

import spimdisasm

from disasm.code_extent import CodeExtent, Decoder, _reg

# Control transfers after which a function may end. `jal` is here because a
# call that never returns (__osCleanupThread: jal osDestroyThread) is the
# last instruction of its function.
TRANSFER = {"jr", "j", "jal", "jalr", "b", "eret"}
NO_DELAY_SLOT = {"eret"}


@dataclass(frozen=True)
class Function:
    vram: int
    size: int            # bytes, excluding trailing padding
    padding: int         # trailing zero bytes cut off as alignment
    seeded: bool         # start was proven by stage 2 before splitting

    @property
    def end(self) -> int:
        return self.vram + self.size


def _body_end(dec: Decoder, start: int, end: int) -> int | None:
    """End of the last real instruction, counting a delay slot as real.

    Trailing zero words are padding only when the last non-zero word is a
    control transfer (its delay slot is kept even when it is a nop: `jr ra;
    nop`) or is that transfer's delay slot. Anything else returns None --
    a run of zeros after ordinary code is not known to be padding.
    """
    last = end - 4
    while last >= start and dec.word(last) == 0:
        last -= 4
    if last < start:
        return None
    op = dec.insn(last).getOpcodeName()
    if op in TRANSFER:
        return last + (4 if op in NO_DELAY_SLOT else 8)
    if last - 4 >= start and dec.insn(last - 4).getOpcodeName() in TRANSFER             and dec.insn(last - 4).getOpcodeName() not in NO_DELAY_SLOT:
        return last + 4
    return None


def split_padding(dec: Decoder, start: int, size: int) -> tuple[int, int]:
    """(size, padding): cut trailing zero words that follow the body's end."""
    end = start + size
    body = _body_end(dec, start, end)
    if body is None or body >= end:
        return size, 0
    return body - start, end - body


ALIGN = 16


def _branch_reaches(dec: Decoder, lo: int, hi: int, target: int) -> bool:
    """Does any branch or j in [lo, hi) target an address >= target?"""
    for v in range(lo, hi, 4):
        d = dec.insn(v)
        if (d.isBranch() or (d.isJump() and d.isJumpWithAddress()))                 and not d.isFunctionCall():
            try:
                if d.getBranchVramGeneric() >= target:
                    return True
            except (RuntimeError, ValueError):
                continue
    return False


def _tears_down_frame(dec: Decoder, lo: int, hi: int) -> bool:
    """An epilogue, not a leaf: restores ra from the stack or pops a frame.

    IDO keeps the unreachable epilogue after an infinite loop (gameThreadMain,
    schedulerThreadMain), separated from the loop's `b` by a nop -- the same
    shape as padding followed by a leaf. A leaf never pops a frame it did not
    push, so this tells them apart.
    """
    for v in range(lo, hi, 4):
        d = dec.insn(v)
        op = d.getOpcodeName()
        if op == "addiu" and _reg(d.rt) == "sp" and _reg(d.rs) == "sp"                 and d.getProcessedImmediate() > 0:
            return True
        if op in ("lw", "ld") and _reg(d.rt) == "ra" and _reg(d.rs) == "sp":
            return True
    return False


def padding_boundaries(dec: Decoder, lo: int, hi: int,
                       known: set[int] | None = None) -> set[int]:
    """Function starts implied by alignment padding (catalog: text-alignment-padding-is-not-function-body).

    A run of zero words that begins right after a control transfer's delay
    slot and reaches a 16-byte boundary is the gap between two objects'
    .text. The next function starts at the LAST boundary at or before its
    first non-zero word: zeros past it are the function's own leading nops
    (libkmc __muldi3 begins with `nop`); zeros before it are padding (SBK1's
    entry segment fills 24 bytes). The boundary is accepted when either

    * the code after the zeros opens a stack frame (`addiu sp, sp, -N`), or
    * it walks as a complete leaf to a return AND no branch in the preceding
      function (from the nearest `known` start) reaches it -- that guard is
      what keeps an early return followed by an executed nop from splitting --
      and it does not pop a frame (`_tears_down_frame`), which is what keeps
      an infinite loop's dead epilogue from splitting.

    Zeros after ordinary code (an IDO -mips1 load-delay nop) never qualify:
    the run must start right after a control transfer's delay slot.
    """
    known = known or set()
    starts = sorted(known)
    out = set()
    v = lo + 4
    while v < hi:
        if dec.word(v) == 0 or dec.word(v - 4) != 0:
            v += 4
            continue
        y = v                                   # first non-zero after the run
        z = y - 4
        while z > lo and dec.word(z - 4) == 0:
            z -= 4
        v += 4
        if z <= lo:
            continue
        # The run may begin with the transfer's own delay-slot nop (`jr ra; nop`).
        body = _body_end(dec, max(lo, z - 8), y)
        s = y // ALIGN * ALIGN                  # last boundary at/before the code
        if body is None or body > s or body >= y:
            continue    # not after a transfer, no boundary, or no padding at all
                        # (a call's own delay-slot nop: SBK2 __umoddi3)
        d = dec.insn(y)
        prologue = (d.getOpcodeName() == "addiu" and _reg(d.rt) == "sp"
                    and _reg(d.rs) == "sp" and d.getProcessedImmediate() < 0)
        if not prologue:
            i = bisect.bisect_right(starts, z - 4) - 1
            prev = starts[i] if i >= 0 else lo
            w = dec.walk(s)
            if w.end is None or _branch_reaches(dec, prev, z, s)                     or _tears_down_frame(dec, s, w.end):
                continue
        out.add(s)
    return out


class Functions(list):
    """The split, plus the spimdisasm section that produced it (for emission)."""
    section = None


def find(rom: bytes, extent: CodeExtent, seeds: set[int] | None = None) -> Functions:
    seg = extent.segment
    text_rom_end = extent.rom(extent.text_end)
    ctx = spimdisasm.common.Context()
    ctx.changeGlobalSegmentRanges(seg.rom_start, seg.rom_end, seg.vram, seg.vram_end)
    dec = Decoder(rom, seg)
    if seeds is None:
        seeds = set(extent.functions) | padding_boundaries(
            dec, seg.vram, extent.text_end, set(extent.functions))
    for v in seeds:
        # isAutogenerated: binary-derived, exactly like spimdisasm's own jal
        # symbols. A plain addFunction() symbol is neither user-declared nor
        # autogenerated, so isTrustableFunction() is False for it -- and it
        # also displaces the trusted symbol spimdisasm would have made from
        # the jal. Seeding 0x80000450 that way deleted initControllerSubsystem.
        ctx.globalSegment.addFunction(v, isAutogenerated=True,
                                      vromAddress=extent.rom(v))
    sec = spimdisasm.mips.sections.SectionText(
        ctx, seg.rom_start, text_rom_end, seg.vram, seg.name, rom,
        seg.rom_start, None)
    sec.analyze()

    out = Functions()
    out.section = sec
    for sym in sec.symbolList:
        size, pad = split_padding(dec, sym.vram, sym.sizew * 4)
        out.append(Function(sym.vram, size, pad, sym.vram in seeds))
    return out

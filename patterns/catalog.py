"""Catalog of IDO codegen patterns.

Where there is a pattern there is a function, and where there is a function
there is use. Every recurring shape we identify in compiled output gets an
entry here rather than an ad-hoc fix buried in whatever module first tripped
over it.

An entry earns its place by being *actionable* in one of three ways:

    evidence  - it changes how raw observations should be interpreted
                (a bulk-copy word says nothing about field width)
    solver    - it tells the refine loop what C shape produces this asm
                (a magic multiply means `/ 10` in the source)
    review    - it is a signal a human or model should weigh, not a rule

Each entry records where it was CONFIRMED. A pattern asserted from intuition
and never checked against the finished decomp is exactly the class of bug this
project keeps catching in itself -- see "How to not ship analysis bugs" in
CLAUDE.md. `confirmed_on` empty means "hypothesis", and hypotheses do not get
to change behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Pattern:
    id: str
    name: str
    kind: str                    # evidence | solver | review
    looks_like: str              # the shape in compiled output
    means: str                   # what it actually is in the source
    prescription: str            # what code should do about it
    confirmed_on: list[str] = field(default_factory=list)
    detector: Callable | None = None

    @property
    def is_hypothesis(self) -> bool:
        return not self.confirmed_on


CATALOG: dict[str, Pattern] = {}


def register(p: Pattern) -> Pattern:
    if p.id in CATALOG:
        raise ValueError(f"duplicate pattern id: {p.id}")
    CATALOG[p.id] = p
    return p


# ------------------------------------------------------------------ confirmed

register(Pattern(
    id="bulk-struct-copy",
    name="Struct assignment compiled to a word-wise copy run",
    kind="evidence",
    looks_like="Three or more width-4 accesses, same direction, same base, at "
               "stride-4 consecutive offsets, interleaved load/store.",
    means="A whole-struct assignment. The words are a memcpy, not field accesses.",
    prescription="Exclude these accesses from field-width inference. They carry "
                 "no information about the layout they happen to traverse.",
    confirmed_on=[
        "SBK1 RaceUiPodiumTrailActor.copyBlock (Transform3D at 0x24, first "
        "member is s16, copied word-wise at 0x34/0x38/0x3C while a genuine "
        "lh reads 0x24)",
    ],
))

register(Pattern(
    id="signedness-cast",
    name="Same location loaded both signed and unsigned",
    kind="review",
    looks_like="lh and lhu (or lb/lbu) at one base+offset.",
    means="An ordinary C cast, e.g. reading an s16 field through (u16). The "
          "field has one type; the reads differ.",
    prescription="Do NOT treat as a type conflict. Report as a note. Prefer the "
                 "declared width; signedness needs other evidence.",
    confirmed_on=["SBK1: fires on 8 locations across a 100%-matched codebase"],
))

register(Pattern(
    id="union-polymorphic-offset",
    name="One offset legitimately carries several widths",
    kind="evidence",
    looks_like="Conflicting widths at a single base+offset that survive "
               "bulk-copy filtering.",
    means="A union, or a padding array overlaying named fields.",
    prescription="Model acceptable widths as a SET, never a single value. A "
                 "width outside the set is a finding; one inside is not.",
    confirmed_on=[
        "SBK1 RaceUiDualCounterActor@0x18 (s8 row / s16 alpha18)",
        "SBK1 CourseSelectWidgetActor@0x1c (u8 pad18[4] / s16 spriteIndex)",
    ],
))

register(Pattern(
    id="ra-as-scratch",
    name="$ra allocated as a general-purpose register",
    kind="evidence",
    looks_like="Loads and stores based on $ra in a non-leaf function that has "
               "already spilled ra to the stack.",
    means="IDO's allocator reusing ra once it is safely saved. Nothing to do "
          "with return addresses.",
    prescription="Treat $ra like any other register when resolving bases. Do "
                 "not special-case or discard it.",
    confirmed_on=["SBK1 drawMenuSpriteWithPaletteScale (909 ra-based accesses "
                  "across the game)"],
))

register(Pattern(
    id="hi-lo-address-pair",
    name="lui/addiu or lui/lo-offset global addressing",
    kind="evidence",
    looks_like="lui rX, %hi  then  addiu rX, rX, %lo  (or the lo folded into a "
               "load's own immediate).",
    means="A absolute address of a global. The addiu form yields a pointer that "
          "is then field-indexed; the folded form addresses the symbol directly.",
    prescription="Resolve to global:0xADDR. In the folded form the field offset "
                 "is zero, because the immediate IS the low half of the address.",
    confirmed_on=["SBK1: 9,564 resolved global accesses across 1,259 functions"],
))


# --------------------------------------------- mined from the matched corpus

register(Pattern(
    id="s16-sign-extend",
    name="sll 16 / sra 16 pair",
    kind="solver",
    looks_like="sll rX, rY, 0x10  then  sra rZ, rX, 0x10. Frequently interleaved "
               "in pairs when two values are promoted together.",
    means="Sign-extension of a 16-bit value to 32 bits -- an s16 variable used "
          "in 32-bit arithmetic.",
    prescription="Declare the variable s16, not s32. The pair disappears if the "
                 "source type is already 32-bit, so its presence is direct "
                 "evidence about a declaration.",
    confirmed_on=["SBK1 drawMenuTilemapSprite @ 0x80011f20 "
                  "(sll t8,t4,0x10 / sra t4,t8,0x10); 120 occurrences"],
))

register(Pattern(
    id="signed-div-power-of-2",
    name="Bias-corrected arithmetic shift",
    kind="solver",
    looks_like="bgez rX, +8 ; sra rD, rX, N ; addiu rT, rX, (2^N - 1) ; "
               "sra rD, rT, N. Often preceded by multu/mflo.",
    means="Signed division by 2^N. The bias on the negative path implements "
          "round-toward-zero, which a bare shift does not.",
    prescription="Write `x / 4096`, NOT `x >> 12`. The two differ for negative "
                 "operands and the shift will never match. When preceded by "
                 "multu/mflo it is fixed-point multiply: `(a * b) / 4096`.",
    confirmed_on=["SBK1 updateMainMenuSceneModelTransforms @ 0x80042260 "
                  "(multu s6,t8 / mflo t9 / bgez / sra 0xc / addiu 4095 / sra 0xc); "
                  "28 occurrences"],
))

register(Pattern(
    id="data-decoded-as-code",
    name="Data symbols disassembled as instructions",
    kind="evidence",
    looks_like="Implausible opcode runs (`mfhi mfhi mfhi mfhi`, long `sra` "
               "chains) under symbols named D_*, jtbl_*, or g*Table.",
    means="objdump labelling a data object inside an executable section and "
          "decoding its bytes. Not code at all.",
    prescription="Filter to STT_FUNC symbols via `objdump -t` before extracting "
                 "anything. Noise shaped like evidence is the one thing the "
                 "evidence tier must never contain.",
    confirmed_on=["SBK1: 629 of 2,742 disassembled symbols were data, "
                  "producing 4,561 fabricated mem_access rows (6.7% of the tier). "
                  "Surfaced by patterns/mine.py, not by the golden test -- the "
                  "garbage landed in uncheckable buckets."],
))


register(Pattern(
    id="repeated-store-same-address",
    name="Two stores to one address in a row",
    kind="solver",
    looks_like="`sh rA, 0(rV)` then `sh rB, 0(rV)` -- the same base and offset "
               "stored twice with no intervening load.",
    means="Two SEPARATE C statements assigning to the same variable. IDO does "
          "not coalesce them, so each assignment emits its own store.",
    prescription="Each statement must READ THE VARIABLE BACK, so use compound "
                 "assignment on the variable itself: `x++;` then `x &= 0xFF;`. "
                 "Two statements alone are NOT enough -- if the second uses a "
                 "precomputed local (`x = n; x = n & 0xFF;`) the first store is "
                 "dead and IDO deletes it, emitting one store instead of two. "
                 "The read-back is what keeps the first store live.",
    confirmed_on=[
        "SBK1 randomNextSecondary: target stores twice to gSecondaryRngIndex; "
        "source is `gSecondaryRngIndex++;` then `gSecondaryRngIndex &= 0xFF;`",
        "Negative confirmation: prompted to write two statements, the model "
        "produced `gSecondaryRngIndex = next; gSecondaryRngIndex = next & 0xFF;` "
        "and IDO emitted ONE sh -- dead-store elimination. 87.92%, not a match.",
    ],
))

register(Pattern(
    id="register-mirroring",
    name="Locals named after registers instead of idiomatic C",
    kind="review",
    looks_like="Candidate C declaring u16 t6, t8, t9 and assigning through them "
               "step by step, mirroring the assembly's register sequence.",
    means="The model is transcribing registers rather than recovering source. "
          "Original authors wrote compound assignments on real variables; the "
          "register sequence is the COMPILER's output, not the input.",
    prescription="Prefer direct operations on the actual globals, parameters and "
                 "struct fields. Introduce a local only where one is needed to "
                 "reproduce ordering. Register-shaped C rarely matches, because "
                 "the compiler chose those registers for source it never saw.",
    confirmed_on=["SBK1 randomNextSecondary: model produced 5 register-named "
                  "locals for a 3-line function and plateaued at 86.25%"],
))


register(Pattern(
    id="relocation-mismatch",
    name="Instructions identical, object still not byte-exact",
    kind="solver",
    looks_like="`Score: 99.999% (0 differences)` together with "
               "`Verified exact match: no`. The normalized instruction diff is "
               "empty and the object still differs.",
    means="The relocations differ, not the code. A symbol is referenced by the "
          "wrong name, or through a different expression that yields the same "
          "instructions but a different relocation entry.",
    prescription="Stop tuning the code -- it is already right. Check symbol "
                 "references instead: `arr` vs `&arr[0]`, a local extern that "
                 "should be the project's declaration, a differently-typed "
                 "array. Compare relocation tables, not the instruction diff, "
                 "because the instruction diff cannot see this.",
    confirmed_on=["SBK1 initEndingCreditsCharacterLoopingSparkle: two of three "
                  "draws reached 0 instruction differences and still reported "
                  "'Verified exact match: no'"],
))


# --------------------------------------------------------------- detectors
#
# A detector reads TARGET assembly and says whether this pattern is present.
# That matters because best-of-N beat sequential refinement: guidance has to
# reach the FIRST prompt, so it must be selectable from the target alone, with
# no attempt to diff against yet.
#
# Detectors are deliberately conservative. A hint that fires wrongly is worse
# than one that never fires -- it spends prompt budget telling the model to do
# something the target does not ask for.

_STORE_RE = re.compile(r"^\s*(?:/\*.*?\*/)?\s*(s[bhw])\s+\$?\w+,\s*(-?(?:0x)?[0-9a-fA-F]+)\(\$?(\w+)\)",
                       re.MULTILINE)


def detect_repeated_store(asm: str) -> bool:
    """Two stores to one base+offset with no intervening load of it."""
    seen: dict[tuple[str, str], int] = {}
    for op, off, base in _STORE_RE.findall(asm):
        key = (base, off)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= 2:
            return True
    return False


def detect_s16_sign_extend(asm: str) -> bool:
    return bool(re.search(r"sll\s+\$?\w+,\s*\$?\w+,\s*0x10\b", asm) and
                re.search(r"sra\s+\$?\w+,\s*\$?\w+,\s*0x10\b", asm))


def detect_signed_div(asm: str) -> bool:
    return bool(re.search(r"\bbgez\b", asm) and re.search(r"\bsra\b", asm)
                and re.search(r"addiu\s+\$?\w+,\s*\$?\w+,\s*(?:0x)?[0-9a-fA-F]*(?:7ff|fff|1ff|3ff)\b",
                              asm, re.IGNORECASE))


CATALOG["repeated-store-same-address"].detector = detect_repeated_store
CATALOG["s16-sign-extend"].detector = detect_s16_sign_extend
CATALOG["signed-div-power-of-2"].detector = detect_signed_div


register(Pattern(
    id="narrow-param-homing",
    name="Dead argument store plus andi at function entry",
    kind="solver",
    looks_like="`sw $aN, K($sp)` near entry whose stored value is never read "
               "back, usually followed by `andi $rX, $aN, 0xffff` (or 0xff).",
    means="Parameter N is declared NARROW (u16/s16, or u8/s8 for 0xff). IDO "
          "homes a narrow parameter to its stack slot on entry and re-derives "
          "the clean value with an andi. A parameter typed s32 produces "
          "neither instruction.",
    prescription="Declare that parameter as the narrow type -- u16/s16 for an "
                 "0xffff mask, u8/s8 for 0xff. Widening it to s32 loses both "
                 "the store and the andi and cannot match. To narrow further, "
                 "reassign to the parameter itself (`arg0 &= 0xFFF;`) rather "
                 "than introducing a local, which would move it to another "
                 "register.",
    confirmed_on=[
        "SBK1 setRaceCameraMode(u16, u16): sw a0,0(sp) / andi t6,a0,0xffff and "
        "sw a1,4(sp) / andi t7,a1,0xffff",
        "Negative control: getRaceItemEffectType(s32) emits no home store",
    ],
))

register(Pattern(
    id="stale-a3-not-an-argument",
    name="Dead argument register at a callsite",
    kind="review",
    looks_like="A callsite where $a3 (or a higher arg register) still holds a "
               "value that the callee never reads.",
    means="IDO leaves an incoming argument sitting in the register and never "
          "clears it. It is stale, not a fourth parameter.",
    prescription="Do NOT add a parameter to the callee to explain it. Count "
                 "arity from what the callee actually consumes.",
    confirmed_on=["DECOMPILATION_LEARNINGS.md, IDO parameter homing section, "
                  "recorded from the reference decomp's own matching work"],
))


_SLL_RE = re.compile(r"\bsll\s+\$?(\w+),\s*\$?(\w+),\s*(?:0x)?([0-9a-fA-F]+)")
_HOME_RE = re.compile(r"\bsw\s+\$?(a[0-3]),\s*(-?(?:0x)?[0-9a-fA-F]+)\(\$?sp\)")
_ANDI_RE = re.compile(r"\bandi\s+\$?\w+,\s*\$?(a[0-3]),\s*0x(ffff|ff)\b", re.I)


def detect_narrow_params(asm: str) -> dict[str, int]:
    """Map argument register -> narrow width in bytes, from entry homing.

    `sw $aN, K($sp)` plus `andi $rX, $aN, 0xffff` means parameter N is 16-bit;
    0xff means 8-bit. An s32 parameter emits neither, so absence is meaningful
    too. Confirmed against setRaceCameraMode(u16,u16) and the s32 control
    getRaceItemEffectType.
    """
    head = "\n".join(asm.splitlines()[:14])
    homed = {m.group(1) for m in _HOME_RE.finditer(head)}
    if not homed:
        return {}

    widths: dict[str, int] = {}
    for m in _ANDI_RE.finditer(head):
        reg, mask = m.group(1), m.group(2).lower()
        if reg in homed:
            widths[reg] = 2 if mask == "ffff" else 1
    return widths


def narrow_param_hint(asm: str) -> str:
    widths = detect_narrow_params(asm)
    if not widths:
        return ""
    lines = []
    for reg in sorted(widths):
        idx = int(reg[1])
        w = widths[reg]
        lines.append(f"  parameter {idx} (${reg}) is {w*8}-bit: declare it "
                     f"{'u16/s16' if w == 2 else 'u8/s8'}, not s32")
    return ("\nNARROW PARAMETERS DETECTED (dead home store + andi at entry):\n"
            + "\n".join(lines) +
            "\n  Typing these wider drops both the store and the andi and "
            "cannot match.\n")
_ADDU_RE = re.compile(r"\baddu\s+\$?(\w+),\s*\$?(\w+),\s*\$?(\w+)")


def decode_array_stride(asm: str) -> list[int]:
    """Recover struct sizes from index arithmetic.

    IDO turns `arr[i]` into a shift/add chain that multiplies the index by the
    element size. `sll 2; addu self; sll 2` is (i*4 + i)*4 = i*20, so the
    element is twenty bytes. That is not a hint or a heuristic -- the stride is
    arithmetically encoded in the instructions and can be read straight out.

    This matters because getting it wrong is invisible to the permuter, which
    permutes register allocation and never touches types. A struct declared 18
    bytes instead of 20 sits at 99% forever: SBK1 unlockRelocatableHeapBlock
    produced exactly that, `sll 3; addu; sll 1` (=18) against a target of
    `sll 2; addu; sll 2` (=20), and 300s of permuting moved it nowhere.

    Returns plausible strides, largest first. Conservative: only the shift/add
    forms IDO actually emits are decoded.
    """
    lines = asm.splitlines()
    strides: set[int] = set()

    for i, line in enumerate(lines):
        m = _SLL_RE.search(line)
        if not m:
            continue
        dest, src, sh = m.group(1), m.group(2), int(m.group(3), 16)
        factor = 1 << sh

        # Look ahead a few instructions for `addu dest, dest, src` (i*2^n + i)
        # then an optional second shift.
        for nxt in lines[i + 1:i + 5]:
            a = _ADDU_RE.search(nxt)
            if a and a.group(1) == dest and {a.group(2), a.group(3)} == {dest, src}:
                total = factor + 1
                for tail in lines[i + 1:i + 8]:
                    m2 = _SLL_RE.search(tail)
                    if m2 and m2.group(1) == dest and m2.group(2) == dest:
                        total *= 1 << int(m2.group(3), 16)
                        break
                if 2 <= total <= 4096:
                    strides.add(total)
                break

    return sorted(strides, reverse=True)


def stride_hint(asm: str) -> str:
    strides = decode_array_stride(asm)
    if not strides:
        return ""
    listed = ", ".join(f"{s} (0x{s:x})" for s in strides)
    return (f"\nARRAY STRIDE DECODED FROM THE TARGET: {listed} bytes.\n"
            f"  The index arithmetic multiplies by exactly this, so any struct "
            f"you index must be EXACTLY this size. Pad it to match. A struct of "
            f"the wrong size produces different shift/add constants and can "
            f"never match, no matter how the rest is written.\n")


_FRAME_RE = re.compile(r"\baddiu\s+\$?sp,\s*\$?sp,\s*-(\d+)")


def target_frame_size(asm: str) -> int | None:
    """Bytes of stack frame the target allocates, from its prologue."""
    m = _FRAME_RE.search(asm)
    return int(m.group(1)) if m else None


def frame_hint(asm: str) -> str:
    """State the target's frame size and the levers that move it.

    Frame size is a hard structural mismatch: get it wrong and every stack
    offset in the function shifts, so the diff looks like pervasive
    register/offset noise rather than one localisable error. It is also
    invisible to the permuter, which never resolves stack differences --
    decomp-permuter's own README says `--stack-diffs` is "quite bad at
    resolving stack differences".

    1,874 of SBK1's functions allocate a frame, so this applies nearly
    everywhere, unlike byte-cast shifts which occur 15 times in the whole game.

    Levers are from the OoT IDO 5.3 -O2 guide, which targets this exact
    compiler and optimisation level.
    """
    size = target_frame_size(asm)
    if size is None:
        return ("\nThe target allocates NO stack frame (no `addiu sp,sp,-N`). "
                "Keep the function frameless: avoid locals that must be spilled "
                "and avoid calls.\n")

    return (f"\nTARGET STACK FRAME: {size} bytes (`addiu sp,sp,-{size}`).\n"
            f"  Your frame must be exactly {size}. If it differs, fix that "
            f"FIRST -- every stack offset shifts with it and the diff becomes "
            f"unreadable noise. Known levers, in order of likelihood:\n"
            f"  - number and width of declared locals (each spilled local costs "
            f"a slot; IDO rounds the frame to 8 bytes)\n"
            f"  - `void f(void)` uses 4 MORE bytes than `void f()`\n"
            f"  - an unused local declared LAST does not affect sp\n"
            f"  - naming a common subexpression forces it to a stack home; "
            f"leaving it anonymous can keep it in a register\n"
            f"  Do not try to permute a frame-size mismatch away; the permuter "
            f"does not resolve stack differences.\n")


def hints_for_asm(asm: str) -> str:
    """Guidance that applies to THIS target, assembled from confirmed patterns.

    This is the accumulated-knowledge tool: every prescription that has been
    verified against a real function, applied automatically whenever its shape
    shows up. A pattern with no `confirmed_on` never fires -- a hypothesis does
    not get to steer the solver.
    """
    if not asm:
        return ""

    fired = []
    for p in CATALOG.values():
        if p.is_hypothesis or p.detector is None or p.kind == "evidence":
            continue
        try:
            if p.detector(asm):
                fired.append(p)
        except Exception:
            continue

    if not fired:
        return ""

    lines = ["\nIDIOMS DETECTED IN THIS TARGET (each confirmed on a real match):"]
    for p in fired:
        lines.append(f"- {p.name}: {p.means}")
        lines.append(f"  -> {p.prescription}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- reporting


def summary() -> str:
    lines = []
    by_kind: dict[str, list[Pattern]] = {}
    for p in CATALOG.values():
        by_kind.setdefault(p.kind, []).append(p)

    for kind in ("evidence", "solver", "review"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"\n{kind.upper()} ({len(items)})")
        for p in items:
            mark = "?" if p.is_hypothesis else "*"
            lines.append(f"  [{mark}] {p.id:26} {p.name}")
            if p.is_hypothesis:
                lines.append("        HYPOTHESIS -- not confirmed, must not change behaviour")
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"{len(CATALOG)} patterns catalogued")
    print(summary())
    unconfirmed = [p.id for p in CATALOG.values() if p.is_hypothesis]
    if unconfirmed:
        print(f"\nunconfirmed: {', '.join(unconfirmed)}")

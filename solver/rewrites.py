"""Candidate source rewrites proposed from the oracle's residual.

Two matches were closed by hand after an external review, and both needed a
PAIR of rewrites -- neither reached exact alone:

    updateRaceSplitscreenSelectPlayerCountIcons  loop bound 4->5 + 8 bytes pad
    updateEndingLindaExitUntilPhase3C            swap two args   + 2 bytes pad

Two of those four had no generator in the codebase at all. diffrepair can move
a field, retype one, or reorder a struct; nothing could change a constant or
permute call arguments, so those matches were unreachable however long the
loop ran.

This module proposes rewrites; it decides nothing. Every proposal is handed to
the oracle, and a wrong one simply fails to verify. That is deliberate -- the
residual states what the target does, but mapping an assembly constant back to
the source literal that produced it is inference, and inference here gets
checked rather than trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from solver import diffrepair, signals

MEM = signals.MEM
OPCODE = signals.OPCODE
REGNAME = re.compile(r"\$?\b([av][0-9]|t[0-9]|s[0-8])\b")
IMMEDIATE = re.compile(r"(-?0x[0-9a-fA-F]+|-?\b\d+\b)")


@dataclass
class Rewrite:
    label: str
    kind: str                    # immediate | argswap | layout | width
    apply: Callable[[str], str]

    def __call__(self, code: str) -> str:
        return self.apply(code)


def _num(text: str):
    try:
        return int(text, 16) if text.lower().startswith(("0x", "-0x")) \
            else int(text)
    except ValueError:
        return None


def immediate_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Constants the target uses where the candidate used another.

        -slti at,v1,5      the target compares against 5
        +slti at,v1,4      we wrote 4

    The assembly constant is a fact; WHICH source literal produced it is not,
    so every plausible occurrence is proposed separately and the oracle picks.
    A literal appearing many times is skipped -- rewriting all of them is a
    different edit, and rewriting an arbitrary one is a coin flip.
    """
    out: list[Rewrite] = []
    seen: set[tuple[int, int]] = set()
    pairs, _n, _m = signals._pairs(diff)
    for a, b in pairs:
        oa = OPCODE.match(a).group(1) if OPCODE.match(a) else ""
        ob = OPCODE.match(b).group(1) if OPCODE.match(b) else ""
        if oa != ob or MEM.match(a) or MEM.match(b):
            continue
        if REGNAME.findall(a) != REGNAME.findall(b):
            continue                       # a register difference, not a value
        want = [x for x in IMMEDIATE.findall(a) if _num(x) is not None]
        got = [x for x in IMMEDIATE.findall(b) if _num(x) is not None]
        if len(want) != len(got):
            continue
        for w, g in zip(want, got):
            nw, ng = _num(w), _num(g)
            if nw is None or ng is None or nw == ng or (nw, ng) in seen:
                continue
            seen.add((nw, ng))
            for pattern in (str(ng), hex(ng)):
                # a bare literal, not part of a longer number or identifier
                rx = re.compile(r"(?<![\w.])" + re.escape(pattern) + r"(?![\w.])")
                hits = rx.findall(code)
                if len(hits) != 1:
                    continue               # ambiguous or absent: do not guess
                repl = str(nw) if pattern == str(ng) else hex(nw)
                out.append(Rewrite(
                    f"immediate {pattern} -> {repl}", "immediate",
                    lambda s, _rx=rx, _r=repl: _rx.sub(_r, s, count=1)))
    return out


CALL = re.compile(r"(\w+)\s*\(([^;()]*(?:\([^()]*\)[^;()]*)*)\)\s*;", re.S)


def argswap_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Adjacent call arguments the target loads into the opposite registers.

        -lh a2,0x26(s0)  -lh a1,0x24(s0)      target: a2<-0x26, a1<-0x24
        +lh a1,0x26(s0)  +lh a2,0x24(s0)      ours:   the reverse

    Same opcode, same offsets, destination registers exchanged: the arguments
    are in the wrong order. Which call is not stated, so every call with enough
    arguments gets a proposal per adjacent pair and the oracle decides.
    """
    swapped = False
    pairs, _n, _m = signals._pairs(diff)
    for a, b in pairs:
        ma, mb = MEM.match(a), MEM.match(b)
        if not (ma and mb):
            continue
        if ma.group(1) == mb.group(1) and ma.group(3) == mb.group(3) \
                and ma.group(2) != mb.group(2):
            swapped = True
            break
    if not swapped:
        return []

    out: list[Rewrite] = []
    for m in CALL.finditer(code):
        args = [a.strip() for a in _split_args(m.group(2))]
        if len(args) < 2:
            continue
        for i in range(len(args) - 1):
            new_args = list(args)
            new_args[i], new_args[i + 1] = new_args[i + 1], new_args[i]
            old, new = m.group(0), (m.group(1) + "(" + ", ".join(new_args)
                                    + ");")
            if old == new:
                continue
            out.append(Rewrite(
                f"swap args {i}/{i+1} of {m.group(1)}", "argswap",
                lambda s, _o=old, _n2=new: s.replace(_o, _n2, 1)))
    return out


def _split_args(text: str) -> list[str]:
    """Split a call's argument list on commas at depth zero."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


RELOC_ADDEND = re.compile(r"%(?:hi|lo)\(\s*(\w+)\s*(?:\+\s*(-?(?:0x)?[0-9a-fA-F]+))?\s*\)")


def reloc_padding_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Padding derived from a relocation ADDEND, which diffrepair cannot see.

        -lbu t7,%lo(gRacePlayers+8)(t7)     target reads 8 bytes into the entry
        +lbu t7,%lo(gRacePlayers)(t7)       we read it at 0

    The difference is carried in the relocation's addend rather than in an
    instruction offset operand, so the offset machinery finds nothing at all --
    diffrepair derives zero constraints from this residual. It is nevertheless
    a plain statement that the field accessed at addend M belongs at addend N,
    so N - M bytes are missing ahead of it in the element struct.

    This was the last gap blocking updateRaceSplitscreenSelectPlayerCountIcons,
    whose entire residual is one such line.
    """
    out: list[Rewrite] = []
    seen: set[tuple[str, int]] = set()
    pairs, _n, _m = signals._pairs(diff)
    for a, b in pairs:
        ma, mb = RELOC_ADDEND.search(a), RELOC_ADDEND.search(b)
        if not (ma and mb) or ma.group(1) != mb.group(1):
            continue
        want = _num(ma.group(2)) if ma.group(2) else 0
        got = _num(mb.group(2)) if mb.group(2) else 0
        if want is None or got is None or want == got or want < got:
            continue
        sym, delta = ma.group(1), want - got
        if (sym, delta) in seen:
            continue
        seen.add((sym, delta))

        # the struct body declaring this symbol, e.g. `} gRacePlayers[8];`
        decl = re.search(r"\{([^{}]*)\}\s*" + re.escape(sym) + r"\s*[\[;]", code)
        if not decl:
            continue
        body = decl.group(1)
        fields = diffrepair._fields("struct S {" + body + "};")
        target_field = next((m for m, off, _s in fields if off == got), None)
        if target_field is None:
            continue
        old_line = target_field.group(0)
        pad = (f"{target_field.group('indent')}"
               f"char rpad{got:02x}[{delta:#x}];\n")
        out.append(Rewrite(
            f"{delta} bytes before {sym}.{target_field.group('name')}",
            "layout",
            lambda s, _o=old_line, _p=pad: s.replace(_o, _p + _o, 1)))
    return out


MASK_OP = re.compile(r"^(andi)\s+\$?(\w+),\s*\$?(\w+),\s*(0x[0-9a-fA-F]+|\d+)")
# EXACTLY a byte or halfword mask. `0[xX][fF]{2,4}` also admitted 0xFFF, which
# is a 12-bit mask and NOT a width no-op: dropping it changes meaning, and the
# sweep duly proposed `drop mask 0xFFF` edits that scored better while being
# semantically different. The safety argument for this rewrite is that lhu and
# lbu already zero-extend, and that argument only covers 0xFF and 0xFFFF.
SOURCE_MASK = re.compile(r"\s*&\s*(0[xX](?:[fF]{2}|[fF]{4})|255|65535)\b")


def drop_mask_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Remove a redundant mask the candidate emits and the target does not.

        +andi v0,t6,0xffff       we mask; the target never does
        -sllv t0,t9,t6           and then the target uses the UNMASKED value
        +sllv t0,t9,v0

    An extra instruction displaces every later branch, which is why
    requestRumbleMotorStart shows three "structural" faults for what is really
    one surplus `& 0xFFFF`: its branch targets all shift by four.

    The catalogue already holds the opposite lever -- adding a redundant mask
    to advance IDO's temp FIFO -- and there was no generator for removing one,
    so this residual proposed nothing at all.

    Masking after a narrow load is semantically a no-op (lhu and lbu already
    zero-extend), so dropping one cannot change meaning; if the mask was load
    bearing the candidate simply fails to verify.
    """
    extra_mask = False
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        m = MASK_OP.match(line[1:].strip())
        if m and _num(m.group(4)) in (0xFF, 0xFFFF):
            extra_mask = True
            break
    if not extra_mask:
        return []

    out: list[Rewrite] = []
    for m in SOURCE_MASK.finditer(code):
        span = m.span()
        out.append(Rewrite(
            f"drop mask {m.group(1)} at {span[0]}", "mask",
            lambda s, _a=span[0], _b=span[1]: s[:_a] + s[_b:]))
    return out


BRANCH_POLARITY = {"beq": "bne", "bne": "beq",
                   "beqz": "bnez", "bnez": "beqz"}
WHILE_HEAD = re.compile(r"\bwhile\s*\(([^;{}]*)\)\s*\{")


def _matching_brace(code: str, open_at: int) -> int:
    """Index of the '}' closing the '{' at open_at, or -1."""
    depth = 0
    for i in range(open_at, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def loop_shape_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Move a loop's test from the top to the bottom, as for(;;) + break.

    NEVER emits a `do` token. The per-function build.sh rejects one outright,
    and this project has already established why that is not the obstacle it
    looks like: the guard bans the TOKEN, not the control-flow shape, and
    build.sh's own error says to use `while` or `for` instead. So
    `for (;;) { body; if (!cond) break; }` is the SANCTIONED form, not a
    workaround -- it compiles to a loop with no entry guard, which is exactly
    the shape a bottom-tested loop needs.

    Recorded in the bank as do-while-functions-are-unmatchable (REFUTED) and
    for-break-rewrite-generalises-across-do-while-sites (CONFIRMED across 389
    of 390 sites). A first version of this generator emitted `do`/`while`
    anyway and was rejected by the build in one compile.

        -bne v1,a0,14      the target closes the loop with a bottom test
        +beq v1,v0,30      we guard it at the top instead

    A top-tested `while` emits an entry guard and a backward jump; a
    bottom-tested `do` emits neither, so the two differ in instruction count
    and in every later branch target. On initMenuAssetHandles -- 13
    instructions, stuck at 83.385 -- that is the whole residual, and it is the
    same fault class that dominates the medium and large tiers where nothing
    has ever matched.

    NOT SEMANTICS-PRESERVING, unlike the padding and mask rewrites: a
    bottom-tested loop runs its body at least once, so this changes behaviour
    when the loop could execute zero times. It is proposed because the original
    source frequently DID know the loop runs at least once, and the oracle
    rejects it when that is wrong. Flagged here because the other generators
    can claim safety and this one cannot.
    """
    # The signal is a SURPLUS conditional branch on our side, not an inverted
    # one. Reading the raw diff suggested `-bne` against `+beq`, but the two
    # streams actually pair bne with bne: the `beq` is an EXTRA line with no
    # counterpart, which is exactly the entry guard a top-tested `while` emits
    # and a `do` does not. Counting branches per side sees that; comparing
    # paired opcodes never can, because the surplus line is unpaired by
    # definition.
    def branches(prefix: str) -> int:
        n = 0
        for line in diff.splitlines():
            if not line.startswith(prefix) or line.startswith(prefix * 3):
                continue
            m = OPCODE.match(line[1:].strip())
            if m and m.group(1) in BRANCH_POLARITY:
                n += 1
        return n

    if branches("+") <= branches("-"):
        return []                       # no surplus guard to remove

    out: list[Rewrite] = []
    for m in WHILE_HEAD.finditer(code):
        open_at = code.index("{", m.start())
        close_at = _matching_brace(code, open_at)
        if close_at < 0:
            continue
        cond = m.group(1).strip()
        body = code[open_at + 1:close_at]

        # A `continue` at THIS loop's level makes the rewrite incorrect, not
        # merely unmatching: in a bottom-tested loop it jumps to the condition
        # test, but in for(;;) it jumps to the top and skips the trailing
        # break, turning a terminating loop into an infinite one. The project
        # measured this across all 390 do-while sites in the game and found
        # exactly one such hazard, so it is rare and real.
        if _has_own_level_continue(body):
            continue

        indent = re.match(r"[ \t]*", code[m.start():]).group(0)
        replacement = (f"for (;;)\n{indent}{{{body}"
                       f"{indent}    if (!({cond})) break;\n{indent}}}")
        old = code[m.start():close_at + 1]
        out.append(Rewrite(f"bottom-test loop on ({cond[:30]})", "loopshape",
                           lambda s, _o=old, _n2=replacement:
                           s.replace(_o, _n2, 1)))
    return out


def _has_own_level_continue(body: str) -> bool:
    """True when `continue` belongs to this loop rather than a nested one."""
    depth = 0
    for m in re.finditer(r"\bfor\b|\bwhile\b|\bcontinue\b|\{|\}", body):
        tok = m.group(0)
        if tok in ("for", "while"):
            depth += 1                  # a nested loop claims the next continue
        elif tok == "}" and depth:
            depth -= 1
        elif tok == "continue" and depth == 0:
            return True
    return False


def layout_rewrites(code: str, diff: str) -> list[Rewrite]:
    """Offset, width and ordering repairs, from the existing diffrepair pass."""
    out: list[Rewrite] = []
    repaired, changed, _info = diffrepair.repair(code, diff)
    if changed and repaired != code:
        out.append(Rewrite("diffrepair layout", "layout",
                           lambda s, _r=repaired: _r if s == code else s))
    return out


def propose(code: str, diff: str) -> list[Rewrite]:
    """Every applicable rewrite for this residual, cheapest kind first."""
    return (layout_rewrites(code, diff)
            + reloc_padding_rewrites(code, diff)
            + drop_mask_rewrites(code, diff)
            + loop_shape_rewrites(code, diff)
            + immediate_rewrites(code, diff)
            + argswap_rewrites(code, diff))

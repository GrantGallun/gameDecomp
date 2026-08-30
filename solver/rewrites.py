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
            + immediate_rewrites(code, diff)
            + argswap_rewrites(code, diff))

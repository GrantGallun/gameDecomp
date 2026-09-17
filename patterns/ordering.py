"""Is an `ordering` residual caused by statement order, or by register colouring?

Derived 2026-09-17. See `patterns/catalog.py` `ordering-diff-conflates-causes` for the evidence.

`signals.analyse` classifies a residual as `ordering` whenever the instruction streams differ in
order. That names the SYMPTOM. Two different causes produce it, and they need different instruments:

    STATEMENT ORDER   distinct instructions emitted in a different order. The target's order IS a
                      source statement order, so the diff states the answer -- reorder the
                      statements and it closes. Confirmed: Fstop, 99.999 -> exact.

    REGISTER COLOURING  two identical-looking instructions with their REGISTERS exchanged, e.g.
                      `move s2,zero` / `move s3,zero`. Permuting statements cannot reach this by
                      construction: the statements are already in the right order and IDO assigned
                      the registers differently. Applying the order rule here REGRESSED the function
                      (99.936 -> 99.554) while leaving the exchange untouched. Confirmed:
                      drawCharacterSelectCoursePreviewPanel8.

The discriminator is mechanical. Reconstruct both instruction sequences from a hunk, take the
positions where they differ, erase register names from those instructions, and ask whether the two
sides become the same multiset. If they do, the only difference was WHICH REGISTER -- colouring. If
they do not, the instructions themselves moved -- order.

This is a routing decision, not a repair. It exists so the ordering pass stops spending compiles on
residuals it cannot reach, and so those functions go to the register search instead. It does not
change any output by itself.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass

# MIPS o32 general and special registers, as the normalized object dump spells them.
REGISTERS = frozenset((
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "k0", "k1", "gp", "sp", "fp", "ra",
))
# Longest first so `s10`-style names and two-letter registers win over their prefixes.
_REGISTER_RE = re.compile(r"\b(?:%s)\b" % "|".join(sorted(REGISTERS, key=len, reverse=True)))
_HUNK_RE = re.compile(r"^@@.*?@@.*$")


def normalise_registers(text: str) -> str:
    """Erase register identity and incidental whitespace, keeping structure.

    `move s2,zero` and `move s3,zero` both become `move R,R`; `sw zero,0x60(a0)` and `sw zero,0x14(a0)`
    stay different because the offset is not a register.

    Whitespace is stripped because a unified diff's context lines carry a marker space that the `-`/`+`
    lines in the same hunk may have been padded around, and instruction spacing is not significant.
    Without this, a context line and its identical counterpart on the other side compare unequal and
    every reorder looks like an instruction change.
    """
    return _REGISTER_RE.sub("R", text).strip()


@dataclass(frozen=True)
class Cause:
    """The verdict for one residual."""
    name: str                     # colouring | order | not-a-permutation | no-hunks
    hunks: int
    colouring_hunks: int
    order_hunks: int
    differing: int                # instruction positions that differ, across all hunks
    detail: str = ""

    @property
    def is_colouring(self) -> bool:
        return self.name == "colouring"


def _hunks(diff: str) -> list[list[str]]:
    """Body lines of each hunk, in file order. Markers are kept so target/candidate can be rebuilt."""
    out: list[list[str]] = []
    current: list[str] | None = None
    for line in (diff or "").splitlines():
        if _HUNK_RE.match(line):
            current = []
            out.append(current)
            continue
        if current is None:
            continue                      # the ---/+++ header before the first hunk
        if line[:3] in ("---", "+++"):
            continue
        if line[:1] in (" ", "-", "+", "\\"):
            current.append(line)
    return out


def _sequences(hunk: list[str]) -> tuple[list[str], list[str]]:
    """(target, candidate) instruction sequences for one hunk.

    A context line belongs to BOTH, so it is appended to each in the order it appears -- that is what
    makes a reorder visible as a permutation rather than as an add/remove pair.
    """
    target, candidate = [], []
    for line in hunk:
        marker, text = line[:1], line[1:]
        if marker in (" ", "-"):
            target.append(text)
        if marker in (" ", "+"):
            candidate.append(text)
    return target, candidate


def _hunk_cause(target: list[str], candidate: list[str]) -> tuple[str, int]:
    if collections.Counter(t.strip() for t in target) != collections.Counter(
            t.strip() for t in candidate):
        return "not-a-permutation", 0
    differing = [i for i in range(min(len(target), len(candidate)))
                 if target[i].strip() != candidate[i].strip()]
    if not differing:
        return "order", 0                 # identical sequences: nothing to explain
    # POSITIONAL, not multiset. A first version compared the normalised multisets and called Fstop
    # `colouring`: permuting four distinct stores leaves the same set of stores, so the multisets
    # match and the test says "only the registers moved" when in fact the instructions did. The
    # question is whether the instruction AT a given position is unchanged apart from its registers.
    register_only = all(normalise_registers(target[i]) == normalise_registers(candidate[i])
                        for i in differing)
    return ("colouring" if register_only else "order"), len(differing)


def classify(diff: str) -> Cause:
    """Which cause does this residual's diff indicate?

    `colouring` only when EVERY differing hunk is register-only. A residual with one of each is
    reported as `order`, because the order pass has something real to do and reporting it as
    colouring would suppress that.
    """
    hunks = _hunks(diff)
    if not hunks:
        return Cause("no-hunks", 0, 0, 0, 0, "no unified-diff hunks found")
    colouring = order = differing = 0
    for hunk in hunks:
        target, candidate = _sequences(hunk)
        name, moved = _hunk_cause(target, candidate)
        differing += moved
        if name == "colouring":
            colouring += 1
        elif name == "order":
            order += 1
        else:
            return Cause("not-a-permutation", len(hunks), colouring, order, differing,
                         "a hunk changes the instruction multiset, so this is not a pure reorder")
    name = "colouring" if colouring and not order else "order"
    return Cause(name, len(hunks), colouring, order, differing,
                 "%d/%d hunks register-only" % (colouring, len(hunks)))

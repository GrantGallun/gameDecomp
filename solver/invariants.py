"""Diagnostic invariants and a proposed repair order from normalized object dumps.

These are search heuristics, not object certificates or a proved dependency order.
Call targets omit argument contracts; branch counts omit CFG topology; normalized
listings can hide byte/relocation differences. Only the ordinary verifier accepts
a match. The diff-only helper is approximate even relative to full listings.

HOW EXPERT MATCHERS ORDER THEIR CHOICES
---------------------------------------
Matching practice (the reference decomp's DECOMPILATION_LEARNINGS.md, decomp-permuter usage) works
top-down and does not open a level until the one above matches: calls and signature first ("wrong
argument order produces misleading register-allocation diffs"), then control flow and instruction
count, then the stack frame, then expressions, then operands, and register allocation last ("things
to try by hand once the instruction sequence and control flow already match"). A choice high in
that order can reshuffle lower-level features. Prioritizing it is a hypothesis
to evaluate; it does not prove that every lower-level intervention is wasted.

This experiment also ranks near-matches on listing features: the score's alignment can
rank a candidate with wrong relocations above one without, and on this project five functions
reached score 100.0 without being exact. So candidates are compared lexicographically on the
invariant distances, level by level, and the score only breaks ties. That ordering
does not replace byte verification or establish that a worsening feature is a dead end.

Pure: parses dump text. No compiler, no model.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

LEVELS = ("calls", "control-flow", "frame", "expressions", "operands", "registers")

_REG = re.compile(r"(?<![%\w])\$?(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra|f\d{1,2})\b")
_BRANCH = re.compile(r"^(b[a-z0-9.]*|j|jr)$")
_SAVED = re.compile(r"^(sw|sd|swc1|sdc1)$")
_CALLEE_SAVED = re.compile(r"^\$?(s[0-8]|fp|ra|f2[0-9]|f3[01])$")


@dataclass(frozen=True)
class Insn:
    op: str
    args: str

    @property
    def masked(self) -> str:
        return _REG.sub("R", self.args)


def parse(dump: str) -> list[Insn]:
    out = []
    for line in (dump or "").splitlines():
        line = line.strip()
        if not line or line.endswith(":") or line.startswith((".", "#", "/")):
            continue
        parts = line.split(None, 1)
        out.append(Insn(parts[0], re.sub(r"\s+", "", parts[1]) if len(parts) > 1 else ""))
    return out


@dataclass(frozen=True)
class Invariants:
    instructions: int
    frame: int | None
    saved: frozenset[str]
    calls: tuple[str, ...]
    branches: int

    @classmethod
    def of(cls, insns: list[Insn]) -> "Invariants":
        frame = None
        for insn in insns:
            if insn.op == "addiu" and insn.args.startswith("sp,sp,-"):
                frame = int(insn.args.split("-", 1)[1], 0)
                break
        saved = set()
        for insn in insns:
            if _SAVED.match(insn.op) and insn.args.endswith("(sp)"):
                reg = insn.args.split(",", 1)[0]
                if _CALLEE_SAVED.match(reg):
                    saved.add(reg.lstrip("$"))
        calls = tuple(i.args for i in insns if i.op in ("jal", "jalr"))
        branches = sum(1 for i in insns if _BRANCH.match(i.op))
        return cls(len(insns), frame, frozenset(saved), calls, branches)


def _edits(a: list, b: list) -> int:
    """Insertions + deletions + replacements between two sequences."""
    total = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            total += max(i2 - i1, j2 - j1)
    return total


def distance(target: list[Insn], candidate: list[Insn]) -> tuple[int, ...]:
    """Per-level distance in LEVELS order. All zero means the instruction listings are identical.

    calls        edits between the ordered call-target sequences
    control-flow |difference| in branch/jump count
    frame        frame size differs (1) + callee-saved registers in one set but not the other
    expressions  edits between the mnemonic sequences
    operands     aligned same-mnemonic instructions whose register-masked operands differ
    registers    aligned instructions that differ only in register names
    """
    t, c = Invariants.of(target), Invariants.of(candidate)
    calls = _edits(list(t.calls), list(c.calls))
    flow = abs(t.branches - c.branches)
    frame = int(t.frame != c.frame) + len(t.saved ^ c.saved)
    ops = _edits([i.op for i in target], [i.op for i in candidate])
    operands = registers = 0
    matcher = difflib.SequenceMatcher(None, [i.op for i in target], [i.op for i in candidate],
                                      autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "equal":
            continue
        for a, b in zip(target[i1:i2], candidate[j1:j2]):
            if a.args == b.args:
                continue
            if a.masked != b.masked:
                operands += 1
            else:
                registers += 1
    return (calls, flow, frame, ops, operands, registers)


def hunks(diff: str) -> list[tuple[list[Insn], list[Insn]]]:
    """(target side, candidate side) per hunk of a unified diff of normalized dumps: context lines
    on both sides, `-` lines on the target's, `+` lines on the candidate's."""
    out, t, c, inside = [], [], [], False
    for line in (diff or "").splitlines():
        if line.startswith(("--- ", "+++ ")):
            continue
        if line.startswith("@@"):
            if inside:
                out.append((parse("\n".join(t)), parse("\n".join(c))))
            t, c, inside = [], [], True
            continue
        if not inside:
            continue
        if line.startswith("-"):
            t.append(line[1:])
        elif line.startswith("+"):
            c.append(line[1:])
        else:
            t.append(line[1:])
            c.append(line[1:])
    if inside:
        out.append((parse("\n".join(t)), parse("\n".join(c))))
    return out


def distance_from_diff(diff: str) -> tuple[int, ...]:
    """Approximate diagnostic from a unified diff, NOT the full-listing ``distance``.

    Identical omitted regions do not make sequence edits or set differences additive:
    a call or saved-register store moved across hunks can be counted twice. Alignment
    can also change when context is omitted. Use ``distance`` on complete listings for
    search ranking; this historical approximation cannot certify equality or progress.
    """
    totals = [0, 0, 0, 0, 0, 0]
    branch_delta = 0
    frame_diff = False
    for target, candidate in hunks(diff):
        d = distance(target, candidate)
        t, c = Invariants.of(target), Invariants.of(candidate)
        totals[0] += d[0]
        branch_delta += c.branches - t.branches
        frame_diff |= t.frame != c.frame
        totals[2] += len(t.saved ^ c.saved)
        totals[3] += d[3]
        totals[4] += d[4]
        totals[5] += d[5]
    totals[1] = abs(branch_delta)
    totals[2] += int(frame_diff)
    return tuple(totals)


def level(dist: tuple[int, ...]) -> str | None:
    """The first level that does not match, or None when the listings are identical."""
    for name, value in zip(LEVELS, dist):
        if value:
            return name
    return None


def rank_key(dist: tuple[int, ...], exact: bool, score: float) -> tuple:
    """Smaller is better: exact first, then object truth level by level, then the score."""
    return (0 if exact else 1, *dist, -(score or 0.0))


def summary(target: list[Insn], candidate: list[Insn]) -> dict:
    t, c = Invariants.of(target), Invariants.of(candidate)
    return {"target": {"instructions": t.instructions, "frame": t.frame, "saved": sorted(t.saved),
                       "calls": list(t.calls), "branches": t.branches},
            "current": {"instructions": c.instructions, "frame": c.frame, "saved": sorted(c.saved),
                        "calls": list(c.calls), "branches": c.branches}}


GUIDE = {
    "calls": ("The CALLS differ (target calls {tcalls}; current calls {ccalls}). Fix this before "
              "anything else. Choices: which function is called, argument order and count, "
              "argument types in the prototype, a call inside vs outside a branch, whether the "
              "return value is used."),
    "control-flow": ("The CONTROL FLOW differs (target has {tbr} branches/jumps, current has "
                     "{cbr}). Choices: loop form (for / while / do-while / goto), if/else vs "
                     "early return, condition polarity, which branch comes first, && and || "
                     "splits, switch vs if-chain, a condition tested once vs repeatedly."),
    "frame": ("The STACK FRAME differs (target frame {tframe} saving {tsaved}; current frame "
              "{cframe} saving {csaved}). Choices: how many values stay live across calls, local "
              "arrays or structs on the stack, a temporary that holds a value across a call, "
              "reusing one variable vs separate variables."),
    "expressions": ("Calls, control flow and frame match; the INSTRUCTION SEQUENCE differs. "
                    "Choices: a temporary introduced or removed, a value cached in a local vs "
                    "re-read, indexing vs pointer arithmetic, multiply vs shift, where a cast "
                    "sits, a variable's width or signedness, compound assignment."),
    "operands": ("Instructions match; OPERANDS differ (offsets, immediates, symbols). Choices: "
                 "a struct field's offset or type, an array's element size, a constant's value "
                 "or form, which global is referenced, signed vs unsigned loads."),
    "registers": ("Everything matches except REGISTER NAMES. Choices: declaration order of "
                  "locals, merging or splitting variables, order of independent statements, "
                  "operand order of a commutative operation on variables, reusing a variable."),
}


def guide(lvl: str | None, target: list[Insn], candidate: list[Insn]) -> str:
    if lvl is None:
        return ""
    s = summary(target, candidate)
    t, c = s["target"], s["current"]
    text = GUIDE[lvl].format(tcalls=t["calls"], ccalls=c["calls"], tbr=t["branches"],
                             cbr=c["branches"], tframe=hex(t["frame"]) if t["frame"] else "none",
                             cframe=hex(c["frame"]) if c["frame"] else "none",
                             tsaved=t["saved"], csaved=c["saved"])
    return ("\nFIRST UNMATCHED LEVEL (work top-down: calls, control flow, frame, expressions, "
            f"operands, registers): {lvl.upper()}\n{text}\nPropose branch points of THIS kind "
            "only; lower levels are reshuffled by any change here.\n")

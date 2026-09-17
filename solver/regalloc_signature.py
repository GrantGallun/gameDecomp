"""Register-allocation residuals, split by signature, with a monotone gradient.

The residual classifier counts every instruction that differs only in register
operands as `register_allocation`. That single number hides different causes
with different source-level fixes. On 2026-09-13, 78 pending functions had no
other fault, and they were stuck at +-0.

    commutative_swap   multu a0,v0 vs multu v0,a0: the C operands of * + & | ^ are
                       written in the other order (makeFixedRotationXY)
    temp_vs_variable   t6 vs v1: one build evaluates an expression into a ugen
                       temporary, the other holds a uopt-coloured value (a named
                       local, or a common subexpression) (randomNextObject)
    temp_numbering     t6 vs t7: the ugen temporary sequence is shifted -- one
                       extra or missing expression temporary earlier
    variable_colour    a2 vs a3, v0 vs v1: uopt coloured a value differently
                       (priority/ordering between webs)
    saved_order        s0 vs s1: callee-saved values are numbered differently
    other_register     any other substitution (at, ra, fp ...)

These signatures are OBSERVATIONS of the two object files. The source-level cause
of each is a hypothesis to test by compiling variants, never a conclusion.

`reordered` and `renames` split `register_instructions` into the half that is an ORDER
difference and the half that is a register difference. They exist because the alignment
cannot tell the two apart by itself: `compare` aligns on register-free `shape`, so
`move s2,zero` and `move s3,zero` are interchangeable to it, and a source that emits those
two instructions in the other order is scored as a two-register `saved_order` swap.

Measured 2026-09-17 on the five 99.936 `drawRaceSplitscreenSelectOption*` /
`drawCharacterSelectCoursePreviewPanel*` siblings. Their whole residual is

    -move    s2,zero        +move    s3,zero
     move    s3,zero        +move    s2,zero

which `signals.analyse` books as `ordering=2, regalloc=0` and `regalloc_signature` books as
`gradient [0,2,2]`, `signatures {saved_order: 2}`. The second reading is not the truth and
neither is the first: the two dumps are consistent with either story, because both
instructions write the constant zero. What IS decidable is the arithmetic. Within one
shape-equal aligned block,

    reordered = common - same_position      instructions present on both sides, at a
                                            different position: an order difference
    renames   = block_length - common       instructions that still differ after pairing
                                            identical text first: a real register difference
    reordered + renames == register_instructions

and on those five, `reordered=2, renames=0`. So the residual is an order difference that
the register gradient cannot express as one, and `regalloc_search` has no signal for the
direction it needs. Both counters are additive diagnostics: `gradient`, `signatures` and
`substitutions` are unchanged, so no existing ranking moves.

`gradient(target, candidate)` is lexicographic and lower is better:
(non-register differences including inserted or deleted instructions, register-differing
instructions, differing register operands). A variant that lowers it moved toward
the target; exactness still comes only from the object comparison.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import difflib
import re

REGISTERS = frozenset(
    "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 t8 t9 s0 s1 s2 s3 s4 s5 s6 s7 s8 "
    "k0 k1 gp sp fp ra".split()) | {f"f{i}" for i in range(32)}
TEMPS = frozenset(f"t{i}" for i in range(10))
VALUES = frozenset("v0 v1 a0 a1 a2 a3".split())
SAVED = frozenset(f"s{i}" for i in range(9))
COMMUTATIVE = frozenset("addu add and or xor nor mult multu".split())
BRANCH = re.compile(r"^(?:b\w*|j|jal|jr|jalr)$")
MEMORY = re.compile(r"^(?P<offset>[^()]*)\((?P<base>\$?\w+)\)$")


@dataclass(frozen=True)
class Instruction:
    mnemonic: str
    operands: tuple[str, ...]

    @property
    def text(self) -> str:
        return f"{self.mnemonic} {','.join(self.operands)}".strip()

    def registers(self) -> list[tuple[int, str]]:
        """(operand position, register) for every register operand, memory bases included."""
        found = []
        last = len(self.operands) - 1
        target_operand = BRANCH.match(self.mnemonic) and self.mnemonic not in ("jr", "jalr")
        for index, operand in enumerate(self.operands):
            if target_operand and index == last:
                continue                                 # branch target, even when it spells "a0"
            memory = MEMORY.match(operand)
            name = (memory.group("base") if memory else operand).lstrip("$")
            if name in REGISTERS:
                found.append((index, name))
        return found

    def shape(self) -> tuple:
        """The instruction with registers replaced by a placeholder."""
        ops = list(self.operands)
        for index, name in self.registers():
            memory = MEMORY.match(ops[index])
            ops[index] = f"{memory.group('offset')}(R)" if memory else "R"
        return (self.mnemonic, tuple(ops))


def parse(text: str) -> list[Instruction]:
    rows = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.endswith(":") or line.startswith((".", "glabel")):
            continue
        parts = line.split(None, 1)
        operands = tuple(o.strip() for o in parts[1].split(",")) if len(parts) > 1 else ()
        rows.append(Instruction(parts[0], operands))
    return rows


def register_class(name: str) -> str:
    if name in TEMPS:
        return "temp"
    if name in VALUES:
        return "value"
    if name in SAVED:
        return "saved"
    return "other"


def classify_pair(target: Instruction, candidate: Instruction) -> list[dict]:
    """Signatures for one aligned instruction pair that differs only in registers."""
    want = [r for _i, r in target.registers()]
    got = [r for _i, r in candidate.registers()]
    if target.mnemonic in COMMUTATIVE and want != got:
        # multu x,y  /  addu d,x,y : the commuting operands are the last two.
        if len(want) >= 2 and want[:-2] == got[:-2] and want[-2:] == got[-2:][::-1]:
            return [{"signature": "commutative_swap", "target": want[-2:], "candidate": got[-2:]}]
    rows = []
    for a, b in zip(want, got):
        if a == b:
            continue
        ca, cb = register_class(a), register_class(b)
        if {ca, cb} == {"temp"}:
            kind = "temp_numbering"
        elif "temp" in (ca, cb) and {ca, cb} & {"value", "saved"}:
            kind = "temp_vs_variable"
        elif ca == cb == "saved":
            kind = "saved_order"
        elif {ca, cb} <= {"value", "saved"}:
            kind = "variable_colour"
        else:
            kind = "other_register"
        rows.append({"signature": kind, "target": a, "candidate": b})
    return rows


@dataclass
class Report:
    target_count: int
    candidate_count: int
    non_register: int = 0
    register_instructions: int = 0
    register_operands: int = 0
    reordered: int = 0
    renames: int = 0
    signatures: Counter = field(default_factory=Counter)
    rename_signatures: Counter = field(default_factory=Counter)
    substitutions: Counter = field(default_factory=Counter)
    differences: list[dict] = field(default_factory=list)

    @property
    def gradient(self) -> tuple[int, int, int]:
        # non_register already covers inserted/deleted instructions (the larger
        # side of each unaligned block), so the count difference is not re-added.
        return (self.non_register, self.register_instructions, self.register_operands)

    @property
    def exact_shape(self) -> bool:
        return self.gradient == (0, 0, 0)

    @property
    def order_only(self) -> bool:
        """Every register-differing position is explained by an instruction that moved.

        A sufficient condition for "this residual needs an order fix, not a register fix",
        and it is arithmetic on the two dumps rather than a hypothesis: the two blocks hold
        the same instructions and only their positions differ. `reordered + renames` is
        `register_instructions` by construction, so this is exactly `renames == 0`.
        """
        return self.register_instructions > 0 and self.renames == 0

    def to_dict(self, limit: int = 40) -> dict:
        return {"gradient": list(self.gradient), "non_register": self.non_register,
                "register_instructions": self.register_instructions, "register_operands": self.register_operands,
                "reordered": self.reordered, "renames": self.renames, "order_only": self.order_only,
                "instruction_counts": [self.target_count, self.candidate_count],
                "signatures": dict(self.signatures),
                "rename_signatures": dict(self.rename_signatures),
                "substitutions": {f"{a}->{b}": n for (a, b), n in self.substitutions.most_common(12)},
                "differences": self.differences[:limit]}


def _pairing(want: list[Instruction], got: list[Instruction]) -> tuple[int, int]:
    """(identical at the same position, present on both sides) for one shape-equal block."""
    same = sum(1 for a, b in zip(want, got) if a == b)
    shared = Counter(i.text for i in want) & Counter(i.text for i in got)
    return same, sum(shared.values())


def _unpaired(want: list[Instruction], got: list[Instruction]) -> list[tuple[Instruction, Instruction]]:
    """Pairs left after matching byte-identical instructions first, in original order.

    These are the register differences that no reordering explains, so classifying them is
    what `rename_signatures` counts -- as opposed to `signatures`, which classifies the
    positional pairs and therefore cannot see the difference.
    """
    budget = Counter(i.text for i in want) & Counter(i.text for i in got)
    left_want = []
    for row in want:
        if budget[row.text]:
            budget[row.text] -= 1
        else:
            left_want.append(row)
    budget = Counter(i.text for i in want) & Counter(i.text for i in got)
    left_got = []
    for row in got:
        if budget[row.text]:
            budget[row.text] -= 1
        else:
            left_got.append(row)
    return list(zip(left_want, left_got))


def compare(target_text: str, candidate_text: str) -> Report:
    target, candidate = parse(target_text), parse(candidate_text)
    report = Report(len(target), len(candidate))
    # Align on register-free shapes, so a register difference never breaks alignment.
    matcher = difflib.SequenceMatcher(a=[i.shape() for i in target], b=[i.shape() for i in candidate],
                                      autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            want_block, got_block = target[a0:a1], candidate[b0:b1]
            same, common = _pairing(want_block, got_block)
            report.reordered += common - same
            report.renames += len(want_block) - common
            for want, got in _unpaired(want_block, got_block):
                for row in classify_pair(want, got):
                    report.rename_signatures[row["signature"]] += 1
            for index, (want, got) in enumerate(zip(want_block, got_block)):
                if want == got:
                    continue
                rows = classify_pair(want, got)
                report.register_instructions += 1
                report.register_operands += sum(1 if r["signature"] != "commutative_swap" else 2 for r in rows)
                for row in rows:
                    report.signatures[row["signature"]] += 1
                    if row["signature"] != "commutative_swap":
                        report.substitutions[(row["target"], row["candidate"])] += 1
                report.differences.append({"index": a0 + index, "target": want.text, "candidate": got.text,
                                           "signatures": [r["signature"] for r in rows]})
        else:
            report.non_register += max(a1 - a0, b1 - b0)
            report.differences.append({"index": a0, "kind": op, "target": [i.text for i in target[a0:a1]][:6],
                                       "candidate": [i.text for i in candidate[b0:b1]][:6]})
    return report


def delta(before: Report, after: Report) -> dict:
    """Whether a change moved toward the target, and which signatures it fixed or introduced."""
    verdict = ("exact_shape" if after.exact_shape else
               "better" if after.gradient < before.gradient else
               "worse" if after.gradient > before.gradient else "same")
    fixed = before.signatures - after.signatures
    introduced = after.signatures - before.signatures
    return {"verdict": verdict, "gradient": [list(before.gradient), list(after.gradient)],
            "fixed": dict(fixed), "introduced": dict(introduced)}

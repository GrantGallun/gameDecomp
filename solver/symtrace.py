"""Symbolic execution of MIPS: what each register actually HOLDS.

The idea: instead of reading instructions, read VALUES. Walk the function
maintaining a symbolic expression per register, so a call site prints as

    getRaceCourseSurfaceType(param0->f502, param0->f1c, param0->f24)

rather than as four loads and a jal whose argument mapping has to be
reconstructed by hand.

That specific line is why this exists. Working it out manually on
isRacePlayerRespawnSurfaceValid took three wrong hypotheses -- struct padding,
callee signature, argument order -- each of which moved the score by a fraction
of a point because the real fix was a conjunction of three changes. The trace
states it outright.

Deterministic, evidence-only, no model. Reads disassembly, which is derived from
the binary, so it is safe on held-out functions.

SCOPE, stated honestly: this is straight-line symbolic execution. Branches are
noted but not explored, and at a merge point a register whose value differs
between paths becomes unknown rather than being guessed. It is a reading aid,
not an emulator, and it does not evaluate arithmetic to concrete values.
"""

from __future__ import annotations

import re
from pathlib import Path

ADDR_COMMENT = re.compile(r"/\*.*?\*/")
ARGREGS = ("a0", "a1", "a2", "a3")
LOAD_W = {"lb": ("s8", 1), "lbu": ("u8", 1), "lh": ("s16", 2),
          "lhu": ("u16", 2), "lw": ("s32", 4), "lwc1": ("f32", 4),
          "ldc1": ("f64", 8)}
STORE_W = {"sb": 1, "sh": 2, "sw": 4, "swc1": 4, "sdc1": 8}


def _clean(line: str) -> str:
    return re.sub(r"\s{2,}", " ", ADDR_COMMENT.sub("", line).strip())


class Trace:
    """Symbolic register state, updated instruction by instruction."""

    def __init__(self) -> None:
        # incoming arguments are the only known-named values at entry
        self.reg: dict[str, str] = {f"a{i}": f"param{i}" for i in range(4)}
        self.reg["sp"] = "sp"
        self.written: set[str] = set()
        self.events: list[str] = []
        self.pending_hi: dict[str, str] = {}

    def val(self, r: str) -> str:
        if r == "zero":
            return "0"
        return self.reg.get(r, f"?{r}")

    def step(self, line: str) -> None:
        m = re.match(r"([a-z][a-z0-9.]*)\s+(.*)$", line)
        if not m:
            return
        op, rest = m.group(1), m.group(2)
        ops = [o.strip().lstrip("$") for o in rest.split(",")]

        # memory: lw $dst, off($base)
        mem = re.match(r"\$?(\w+)\s*,\s*(-?(?:0x)?[0-9A-Fa-f]+)\(\$(\w+)\)$",
                       rest)
        lo = re.match(r"\$?(\w+)\s*,\s*%lo\(([\w.]+)\)\(\$(\w+)\)$", rest)

        if op in LOAD_W and (mem or lo):
            if lo:
                dst, sym = lo.group(1), lo.group(2)
                self.reg[dst] = f"{sym}"
            else:
                dst, off, base = mem.group(1), mem.group(2), mem.group(3)
                b = self.val(base)
                self.reg[dst] = (f"{b}->f{int(off, 0):x}" if b != "sp"
                                 else f"stack[{off}]")
                self.written.add(dst)
            return

        if op in STORE_W and (mem or lo):
            if lo:
                src, sym = lo.group(1), lo.group(2)
                self.events.append(f"{sym} = {self.val(src)}")
            else:
                src, off, base = mem.group(1), mem.group(2), mem.group(3)
                b = self.val(base)
                where = (f"{b}->f{int(off, 0):x}" if b != "sp"
                         else f"stack[{off}]")
                self.events.append(f"{where} = {self.val(src)}")
            return

        if op == "lui":
            m2 = re.search(r"%hi\(([\w.]+)\)", rest)
            if m2 and ops:
                # Commit the address NOW. A %lo often never arrives -- when a
                # symbol's low half is zero, `lui` alone forms the whole
                # address, and addRenderCallback's first argument was being
                # reported as param0 because the pending %hi was never applied.
                # A following `addiu %lo` simply overwrites this with the same
                # symbol.
                self.pending_hi[ops[0]] = m2.group(1)
                self.reg[ops[0]] = f"&{m2.group(1)}"
                self.written.add(ops[0])
            return

        if op == "addiu" and "%lo" in rest and len(ops) >= 2:
            m2 = re.search(r"%lo\(([\w.]+)\)", rest)
            if m2:
                self.reg[ops[0]] = f"&{m2.group(1)}"
            return

        if op in ("move", "or") and len(ops) >= 2:
            src = ops[1] if ops[1] != "zero" else (ops[2] if len(ops) > 2
                                                   else "zero")
            self.reg[ops[0]] = self.val(src)
            self.written.add(ops[0])
            return

        if op == "jal":
            callee = rest.strip()
            args = [self.val(r) for r in ARGREGS]
            # Trailing argument registers are ambiguous and must not be
            # guessed. a3 here holds param0 because of `move a3,a0` -- a
            # scratch copy of the pointer, not a fourth argument. "Was it
            # written" does not distinguish the two, and neither does anything
            # else visible inside this function.
            note = ""
            while len(args) > 1:
                last = args[-1]
                # "?a3" means the register was never set on this path, or was
                # clobbered by an earlier call. Either way it is not an
                # argument, and printing it invents one.
                if last.startswith("?") or last == f"param{len(args)-1}" \
                        or last in args[:-1]:
                    args.pop()
                    note = "   /* trailing arg regs dropped: unset, or a copy "
                    "of an earlier one */"
                else:
                    break
            self.events.append(f"CALL {callee}({', '.join(args)}){note}")
            self.reg["v0"] = f"{callee}(...)"
            return

        if op in ("addiu", "addi") and len(ops) >= 3 and ops[0] == "sp":
            return          # frame setup; sp stays sp so stores read as stack[]

        if op in ("addiu", "addu", "addi") and len(ops) >= 3:
            a, b = self.val(ops[1]), ops[2].lstrip("$")
            b = self.val(b) if not re.match(r"-?\d|0x", b) else b
            self.reg[ops[0]] = (b if a == "0" else
                                (a if b == "0" else f"({a} + {b})"))
            return

        if op in ("sll", "sra", "srl") and len(ops) >= 3:
            self.reg[ops[0]] = f"({self.val(ops[1])} {op} {ops[2]})"
            return

        if op.startswith("b") and op != "break":
            self.events.append(f"branch {op} {rest}")
            return

        if len(ops) >= 2 and re.match(r"[a-z]", ops[0] or ""):
            self.reg[ops[0]] = f"{op}({', '.join(self.val(o) for o in ops[1:])})"


CALLER_SAVED = tuple(f"v{i}" for i in range(2)) + \
               tuple(f"a{i}" for i in range(4)) + \
               tuple(f"t{i}" for i in range(10))


def trace(asm: str, max_events: int = 60) -> list[str]:
    """Walk the function, honouring the delay slot and clobbering across calls.

    Two corrections over the first version, both real:

    DELAY SLOT. The instruction AFTER a jal executes BEFORE the call, and IDO
    routinely puts argument setup there. Emitting the CALL event on sight of
    the jal read arguments one instruction too early. callsig already looked
    into the delay slot; this did not.

    CALLER-SAVED CLOBBER. v0/v1, a0-a3 and t0-t9 do not survive a call. Without
    invalidating them, a stale value from before the call leaks into every
    later event and reads as though it were still live.
    """
    t = Trace()
    lines = [_clean(l) for l in asm.splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("glabel") or line.endswith(":"):
            if line.endswith(":"):
                t.events.append(line)
            i += 1
            continue

        if re.match(r"jal\s", line) or re.match(r"jalr\s", line):
            # run the delay slot first, then the call
            if i + 1 < len(lines) and lines[i + 1]:
                t.step(lines[i + 1])
            t.step(line)
            for r in CALLER_SAVED:
                if r not in ("v0",):        # v0 is set by the call itself
                    t.reg.pop(r, None)
                    t.written.discard(r)
            i += 2
            continue

        t.step(line)
        i += 1
    return t.events[:max_events]


def render(asm: str) -> str:
    """A prompt-ready value-level view of the function."""
    ev = trace(asm)
    if not ev:
        return ""
    return ("VALUE TRACE (what each register holds, derived from the "
            "instructions):\n  " + "\n  ".join(ev))


if __name__ == "__main__":
    import sys
    from solver import workspace
    repo = Path.home() / "decomp/sbk1"
    fn = sys.argv[1] if sys.argv[1:] else "isRacePlayerRespawnSurfaceValid"
    ws = workspace.bootstrap(repo, fn)
    print(render(workspace.target_asm(ws, fn)))

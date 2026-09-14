"""Compile a candidate to IDO's pre-`as1` assembly and diff at that layer.

From the reference project's DECOMPILATION_LEARNINGS.md:

    Read IDO's pre-as1 output before theorising about an allocation residual.
    `cc -S` writes the assembly that ugen produced: final register numbers,
    .loc line records, and unexpanded pseudo-ops. That is the layer where
    register allocation is decided, so a "schedule" difference in the object is
    often a register difference upstream -- as1 reorders around the physical
    registers it is handed. Diffing two candidates' -S output isolates one
    changed decision where diffing their objects shows dozens of shifted rows.

That matters here because register allocation is our largest untouched fault
class: renderRaceUiSingleTrailEffect has 45 residual faults, ZERO of them
structural, and 36 of them register choice. Every pass built so far -- repad,
reorder_fields, width repair, tracefix -- moves a field or retypes one. None
of them can move a value into a different register.

WHAT THIS IS AND IS NOT
    It compares two CANDIDATES, not a candidate against the target. The target
    exists only as a post-as1 object, so its rows are already reordered around
    its registers; there is no target -S to diff against. What this buys is a
    clean read on what a source nudge actually DID -- one allocation decision,
    or many -- which is the feedback the score cannot give.

The flags mirror the per-function workspace build exactly (minus -c, plus -S),
because a different translation unit is a different program -- a lesson this
project has already paid for.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import tempfile
from pathlib import Path

# From nonmatchings/<func>/build.sh. Kept verbatim rather than paraphrased:
# these decide the program, and -Wab,-r4300_mul in particular changes codegen.
CFLAGS = [
    "-mips1", "-G", "0", "-non_shared", "-fullwarn", "-Xcpluscomm",
    "-nostdinc", "-Wab,-r4300_mul", "-woff", "649,838,712,516",
]
C_DEFINES = ["-DLANGUAGE_C", "-D_LANGUAGE_C", "-D_MIPS_SZLONG=32", "-DNDEBUG"]

# `.loc 2 17` and the echoed source line vary with formatting, not codegen.
NOISE = re.compile(r"^\s*(\.loc\b|#\s|\.file\b|\.verstamp\b)")
REGISTER = re.compile(r"\$(\d+|[a-z][a-z0-9]*)")


def compile_s(repo: Path, code: str, opt: str = "-O2",
              timeout: int = 120) -> tuple[str, str]:
    """(assembly, stderr) for `code` at the layer where allocation is decided."""
    repo = Path(repo).expanduser()
    cc = repo / "tools" / "ido-recomp" / "linux" / "cc"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "cand.c"
        # cfe warns without one, and the warning is pure noise in the log
        src.write_text(code if code.endswith("\n") else code + "\n")
        # IDO's cc IGNORES -o under -S and writes <basename>.s into the working
        # directory, so run in the temp dir and read it from there. Passing -o
        # silently produced nothing at all, which read as a compile failure.
        out = Path(td) / "cand.s"
        cmd = [str(cc), opt, *CFLAGS, f"-I{repo / 'include'}", *C_DEFINES,
               "-S", str(src)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=td, timeout=timeout)
        if not out.exists():
            return "", proc.stderr or proc.stdout
        return out.read_text(errors="replace"), proc.stderr


def instructions(asm: str) -> list[str]:
    """Assembly lines that carry codegen, with line-record noise removed."""
    out = []
    for line in asm.splitlines():
        if NOISE.match(line):
            continue
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        out.append(body)
    return out


def decisions(asm: str) -> list[tuple[str, tuple[str, ...]]]:
    """(opcode, registers) per instruction -- the allocation decisions."""
    out = []
    for line in instructions(asm):
        parts = line.split(None, 1)
        if not parts or parts[0].startswith("."):
            continue
        regs = tuple(REGISTER.findall(parts[1])) if len(parts) > 1 else ()
        out.append((parts[0], regs))
    return out


def diff(a: str, b: str) -> list[str]:
    """Unified diff of two candidates at the pre-as1 layer."""
    return [l for l in difflib.unified_diff(
        instructions(a), instructions(b), "before", "after", lineterm="")
        if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def register_changes(a: str, b: str) -> list[tuple[str, tuple, tuple]]:
    """Instructions whose OPCODE is unchanged but whose registers moved.

    This is the signal the object diff destroys: when allocation shifts, as1
    reorders around it and every later row moves, so the object shows dozens of
    differences for one upstream decision.
    """
    da, db = decisions(a), decisions(b)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, [op for op, _ in da], [op for op, _ in db],
            autojunk=False).get_opcodes():
        if tag != "equal":
            continue
        for (op, ra), (_op2, rb) in zip(da[i1:i2], db[j1:j2]):
            if ra != rb:
                out.append((op, ra, rb))
    return out

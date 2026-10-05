"""The control-flow skeleton of a compiled function, and its distance from the target's.

The skeleton is the ordered sequence of control transfers: each branch or jump as its mnemonic plus whether it goes
backward (^) or forward (v), and each `jr` (a return or a jump table). Registers, operands, offsets and every
non-branch instruction are ignored. So the skeleton isolates the shape of the C (loop forms, if/else layout, early
returns, && chains) from types, temporaries and register allocation. Two spellings with the same skeleton are the
same shape to IDO. Distance 0 is necessary for an exact match, not sufficient.

Built from the oracle's unified diff. The normalized dumps are one instruction per line, line 1 at offset 0, so every
instruction's offset is recoverable from the hunk headers, even across the regions the diff skips.
"""
from __future__ import annotations

import difflib
import re

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
BRANCH = re.compile(r"^(b[a-z0-9]*|j)\s+(?:.*,)?\s*([0-9a-f]+)\s*$")
NOT_BRANCH = {"break", "bal", "bgezal", "bltzal"}


def _token(instr: str, offset: int) -> str | None:
    op = instr.split(None, 1)[0] if instr.split() else ""
    if op == "jr":
        return "jr"
    if op in NOT_BRANCH or op in ("jal", "jalr"):
        return None
    m = BRANCH.match(instr)
    if not m:
        return None
    target = int(m.group(2), 16)
    return m.group(1) + ("^" if target <= offset else "v")


def skeletons(diff: str) -> tuple[list[str], list[str]]:
    """(target skeleton, candidate skeleton) from an oracle diff."""
    t, c = [], []
    tl = cl = None
    for line in (diff or "").splitlines():
        h = HUNK.match(line)
        if h:
            tl, cl = int(h.group(1)), int(h.group(2))
            continue
        if line.startswith(("---", "+++")) or not line or tl is None:
            continue
        body = line[1:].strip()
        if line[0] in " -":
            tok = _token(body, (tl - 1) * 4) if body else None
            if tok:
                t.append(tok)
            tl += 1
        if line[0] in " +":
            tok = _token(body, (cl - 1) * 4) if body else None
            if tok:
                c.append(tok)
            cl += 1
    return t, c


def distance(diff: str) -> int:
    """Unmatched skeleton tokens on either side (0 = same control-flow shape as far as branches show)."""
    t, c = skeletons(diff)
    matched = sum(b.size for b in difflib.SequenceMatcher(None, t, c, autojunk=False).get_matching_blocks())
    return len(t) + len(c) - 2 * matched

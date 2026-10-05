"""m2c's copy-back temporary (`T = E; X = T; ... T ...`) back into `X = E; ... X ...`.

Confirmed rule (catalog `ido53-o1-copyback-temporary`, eval/results/temp-copyback-20260929): at -O1 every
local has a stack home, so the temporary is stored and reloaded (`sw t8,24(sp)` ... `lw t1,24(sp)`) where the
direct form compares straight from the register. m2c writes a loop counter `i++` as `temp_t5 = i + 1; i = temp_t5;
... if (!(temp_t5 < n)) break;`, which found no owner: `rewrites.inline_temporary_rewrites` needs a declaration
initialiser and a single read, and is gated to register-only residuals. Found reading osMotorStart after the
unaligned-copy repair (frame 0x60 against the target's 0x50).

A merge is proposed only when it can't change meaning: T is not read before the pair, and every later read of
T comes before the next assignment to X or T.
"""
from __future__ import annotations

import re

_IDENT = r"[A-Za-z_]\w*"


def _stack_heavier(diff: str) -> bool:
    from solver import diffrepair
    target, candidate = diffrepair._streams(diff or "")
    count = lambda s: sum(1 for x in s if re.match(r"^(?:sw|lw)\s+\w+,-?(?:0x)?[0-9a-f]+\(sp\)$", x))
    return count(candidate) > count(target)


def _assigns(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.>])(?:{name}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|(?:\+\+|--)\s*{name}\b|{name}\s*(?:\+\+|--))")


def variants(source: str, function: str, diff: str, limit: int = 8, gated: bool = True):
    """(label, candidate) per mergeable copy-back pair in `function`; gated on extra stack traffic.

    `gated=False` is for rule mining (solver.rewrite_library) on exact sources, which have no residual.
    """
    if gated and not _stack_heavier(diff):
        return
    from solver import branch_shape, c89
    try:
        begin, stop = branch_shape._body(source, function)
    except Exception:
        return
    body = source[begin:stop]
    masked = c89._mask(body)
    pair = re.compile(rf"^(?P<ind>[ \t]*)(?P<t>{_IDENT})\s*=\s*(?P<e>[^;=][^;]*);[ \t]*\n[ \t]*(?P<x>{_IDENT})\s*=\s*(?P=t)\s*;[ \t]*\n", re.M)
    made = 0
    for m in pair.finditer(masked):
        t, x = m["t"], m["x"]
        if t == x:
            continue
        read = re.compile(rf"(?<![\w.>]){t}\b(?!\s*=[^=])")
        decl = re.search(rf"^[ \t]*[\w \t\*]+?\b{t}\s*;[ \t]*\n", masked, re.M)
        before = masked[:m.start()]
        if decl:
            before = before[:decl.start()] + before[decl.end():]
        if read.search(before):
            continue                       # loop-carried or earlier read: meaning could change
        after = masked[m.end():]
        next_write = min([w.start() for w in (_assigns(x).search(after), _assigns(t).search(after)) if w] + [len(after)])
        reads = [r for r in read.finditer(after)]
        if any(r.start() >= next_write for r in reads):
            continue
        new = (body[:m.start()] + f"{m['ind']}{x} = {body[m.start('e'):m.end('e')].strip()};\n"
               + re.sub(rf"(?<![\w.>]){t}\b", x, body[m.end():m.end() + next_write])
               + body[m.end() + next_write:])
        if decl and not re.search(rf"(?<![\w.>]){t}\b", new[:decl.start()] + new[decl.end():]):
            d = re.search(rf"^[ \t]*[\w \t\*]+?\b{t}\s*;[ \t]*\n", new, re.M)
            if d:
                new = new[:d.start()] + new[d.end():]
        yield f"temp_copyback:{t}->{x}", source[:begin] + new + source[stop:]
        made += 1
        if made >= limit:
            return

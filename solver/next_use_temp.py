"""Inline a temporary that is assigned once and read once by the very next statement: `T = E; S(T);` -> `S(E);`.

m2c writes a call's result through a temporary (`temp_v0 = f(x); return temp_v0;`). At -O1 every local has a
stack home, so the temporary is stored and reloaded (`sw v0,0x1c(sp)` / `lw v0,0x1c(sp)`, frame 8 bytes larger)
where the direct form returns straight from v0. No existing generator owned this shape:
`regalloc_mutations.single_use_local_inlines` and `pure_local_inlines` refuse a value containing a call (moving a
call past other code can change meaning), and `temp_copyback` merges `T = E; X = T;` into another variable, not
into an expression. Found on planted single-statement edits (eval/results/edit-capability-20261002: 4 of 6 such
cases unsolved by site_edits, all libultra -O1 functions).

Moving E is safe here by construction: nothing executes between the assignment and the single read, the
temporary is mentioned nowhere else, and its address is never taken. Gated, like temp_copyback, on the candidate
carrying more stack loads/stores than the target. Deterministic and LLM-free; the oracle decides.
"""
from __future__ import annotations

import re

_IDENT = r"[A-Za-z_]\w*"


def variants(source: str, function: str, diff: str, limit: int = 8, gated: bool = True):
    """(label, candidate) per inlinable next-use temporary in `function`."""
    from solver import branch_shape, c89, temp_copyback
    if gated and not temp_copyback._stack_heavier(diff):
        return
    try:
        begin, stop = branch_shape._body(source, function)
    except Exception:
        return
    body, masked = source[begin:stop], c89._mask(source)[begin:stop]
    lines, mlines = body.split("\n"), masked.split("\n")
    count = 0
    for i, mline in enumerate(mlines):
        m = re.match(rf"^\s*({_IDENT})\s*=(?!=)\s*(.+?);\s*$", mline)
        if not m:
            continue
        name = m.group(1)
        j = next((k for k in range(i + 1, len(mlines)) if mlines[k].strip()), None)
        if j is None or len(re.findall(rf"\b{name}\b", mlines[j])) != 1:
            continue
        # Mentioned exactly three times in the body: declaration, assignment, the one read. Never address-taken.
        if len(re.findall(rf"\b{name}\b", masked)) != 3 or re.search(rf"&\s*{name}\b", masked):
            continue
        decl = next((k for k, d in enumerate(mlines)
                     if k != i and re.match(rf"^\s*[A-Za-z_][\w\s]*?[\s*]\**\s*{name}\s*;\s*$", d)), None)
        if decl is None or decl > i:
            continue
        # The read must be a value, not an assignment target.
        if re.search(rf"\b{name}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+\s*{name}\b|\b{name}\s*\+\+", mlines[j]):
            continue
        value = lines[i][lines[i].index("=", m.start(1) + len(name)) + 1:].strip()
        value = value[:-1].strip() if value.endswith(";") else value
        simple = re.fullmatch(rf"{_IDENT}(?:\s*\([^;]*\))?|-?(?:0x[0-9A-Fa-f]+|\d+)", value)
        use = re.search(rf"\b{name}\b", mlines[j])
        replacement = value if simple else f"({value})"
        new_j = lines[j][:use.start()] + replacement + lines[j][use.end():]
        out = [l for k, l in enumerate(lines) if k not in (i, decl)]
        out[j - (1 if decl < j else 0) - (1 if i < j else 0)] = new_j
        yield (f"next_use_temp:{name}", source[:begin] + "\n".join(out) + source[stop:])
        count += 1
        if count >= limit:
            return

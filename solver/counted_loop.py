"""m2c's rotated counted loop back into the `for` loop it came from.

m2c writes `for (i = 0; i < n; i++) { body }` as

    i = 0;
    if (n > 0) {                       (the guard IDO emits for a `for`)
        for (;;) {
            body ... i += 1; ...       (or `temp = i + 1; ... i = temp;`, the break then testing temp)
            if (!(i < n)) break;
        }
    }

and IDO hands temporaries out in a different order for that C than for the `for` it compiled: osMotorStart
and osMotorStop were exact only once the loop was written back as `for` (eval/results/temp-copyback-20260929/
loopshape.py: `for` 100.0 against 99.223 for every other spelling; catalog `ido53-o1-counted-loop-shape`).

Moving the increment to the `for` step is equivalent when nothing after it in the body reads the counter
(reads before it see the same value a `for` body would) and, for the temporary form, nothing between the
temporary's definition and the copy-back reads the temporary or writes the counter. Otherwise it declines.
"""
from __future__ import annotations

import re

_IDENT = r"[A-Za-z_]\w*"


def _statements(text: str) -> list[str]:
    """Top-level statements of a block body (a nested `{...}` stays inside its statement)."""
    out, depth, cur = [], 0, []
    for ch in text:
        cur.append(ch)
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append("".join(cur)); cur = []
        elif ch == ";" and depth == 0:
            out.append("".join(cur)); cur = []
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return [s.strip() for s in out if s.strip()]


def _matching(text: str, open_at: int) -> int | None:
    depth = 0
    for k in range(open_at, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return None


def _reads(stmt: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w.>]){re.escape(name)}\b", stmt))


def _plan(stmts: list[str], i: str, cond_var: str):
    """Indices of the increment statement(s) to drop, or None when moving them to the step is unsafe."""
    inc = re.compile(rf"^(?:{i}\s*(?:\+=\s*1|=\s*{i}\s*\+\s*1)|{i}\s*\+\+|\+\+\s*{i})\s*;$")
    direct = [k for k, s in enumerate(stmts) if inc.match(s)]
    if cond_var == i and len(direct) == 1:
        k = direct[0]
        if any(_reads(s, i) for s in stmts[k + 1:]):
            return None
        return {k}
    if cond_var != i:                                  # temp form: T = i + 1; ... i = T;
        t = cond_var
        defs = [k for k, s in enumerate(stmts) if re.match(rf"^{t}\s*=\s*{i}\s*\+\s*1\s*;$", s)]
        backs = [k for k, s in enumerate(stmts) if re.match(rf"^{i}\s*=\s*{t}\s*;$", s)]
        if len(defs) != 1 or len(backs) != 1 or defs[0] > backs[0]:
            return None
        d, b = defs[0], backs[0]
        if any(_reads(s, t) or re.search(rf"(?<![\w.>]){i}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)", s) for s in stmts[d + 1:b]):
            return None
        if any(_reads(s, i) or _reads(s, t) for s in stmts[b + 1:]):
            return None
        if any(_reads(s, t) for s in stmts[:d]):
            return None
        return {d, b}
    return None


def variants(source: str, function: str, diff: str = "", limit: int = 4):
    """(label, candidate) for each rotated counted loop in `function` that can be written as `for`."""
    from solver import branch_shape
    try:
        begin, stop = branch_shape._body(source, function)
    except Exception:
        return
    body = source[begin:stop]
    head = re.compile(rf"^(?P<ind>[ \t]*)(?P<i>{_IDENT})\s*=\s*0\s*;[ \t]*\n"
                      rf"[ \t]*if\s*\(\s*(?P<n>.+?)\s*>\s*0\s*\)\s*\{{[ \t]*\n"
                      rf"[ \t]*for\s*\(\s*;\s*;\s*\)\s*\{{", re.M)
    made = 0
    for m in head.finditer(body):
        loop_close = _matching(body, m.end() - 1)
        if loop_close is None:
            continue
        guard_open = body.rfind("{", 0, body.rfind("for", 0, m.end()))
        guard_close = _matching(body, guard_open)
        if guard_close is None or body[loop_close + 1:guard_close].strip():
            continue                                   # the guard holds more than the loop
        inner = body[m.end():loop_close]
        stmts = _statements(inner)
        if not stmts:
            continue
        brk = re.match(rf"^if\s*\(\s*!\s*\(\s*(?P<v>{_IDENT})\s*<\s*(?P<n>.+?)\s*\)\s*\)\s*break\s*;$", stmts[-1], re.S)
        i, n = m["i"], m["n"].strip()
        if not brk or brk["n"].strip() != n:
            continue
        drop = _plan(stmts[:-1], i, brk["v"])
        if drop is None:
            continue
        kept = [s for k, s in enumerate(stmts[:-1]) if k not in drop]
        ind = m["ind"]
        new = f"{ind}for ({i} = 0; {i} < {n}; {i}++) {{\n" + "".join(
            "".join(f"{ind}    {line.strip()}\n" for line in s.splitlines() if line.strip()) for s in kept) + f"{ind}}}\n"
        end = guard_close + 1
        if body[end:end + 1] == "\n":
            end += 1
        cand_body = body[:m.start()] + new + body[end:]
        from solver.unaligned_copy import _drop_orphans     # a folded temporary keeps a frame home at -O1
        cand_body = _drop_orphans(cand_body, "\n".join(stmts[k] for k in drop))
        yield f"counted_loop:{i}", source[:begin] + cand_body + source[stop:]
        made += 1
        if made >= limit:
            return

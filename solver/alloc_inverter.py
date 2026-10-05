"""Run IDO 5.3's register allocator backwards: from a mis-coloured live range to the C edits that recolour it.

Input is uopt's own view of the candidate (level-5/6 trace, `solver.uopt_trace`) and the per-range diagnosis
against the target (`solver.uopt_diagnosis`). Each operator comes from a confirmed rule (patterns/catalog.py):

  raise    x wants a colour an EARLIER-coloured range y holds, both constrained: colouring is by priority
           (uopt53-colouring-order), priority = save / units(block span) (uopt53-priority-block-units), a read adds 1
           to save and 10 inside a loop (uopt53-save-counts-reads, uopt53-loop-read-weight), and an empty test adds
           two blocks to every range live across it (uopt53-empty-test-adds-blocks). Emit k empty reads `if (!x);`
           after each assignment of x, for the k the formula gives and the next two (block growth is not simulated).
  earlier  the same, both unconstrained: those are coloured in live-range (first-store) order, so move x's first
           assignment above y's.
  inline   x is a value the target never makes a variable (`ugen_temp`): inline its single assignment.
Ranges map to C variables by declaration order (uopt53-local-offsets: word locals at -4, -8, ...), stopping at the
first local that is not one word. Candidates are hypotheses; the compiler decides.
"""
from __future__ import annotations

import re

KEYWORDS = {"return", "if", "else", "for", "while", "do", "switch", "case", "goto", "break", "continue", "sizeof"}
WORD = re.compile(r"^(?:s32|u32|int|unsigned|long|f32|float)$")
CAP = 12


def _body(source, function):
    from solver import regalloc_mutations
    return regalloc_mutations._body(source, function)


def local_offsets(source: str, function: str) -> dict[int, str]:
    """frame offset -> local name, by declaration order, while every local so far is one word."""
    begin, stop = _body(source, function)
    found, offset = {}, -4
    for m in re.finditer(r"^[ \t]*((?:[A-Za-z_]\w*[ \t]+)+)(\**)[ \t]*([A-Za-z_]\w*)[ \t]*(\[)?[^;\n]*;",
                         source[begin:stop], re.M):
        words = m.group(1).split()
        if words[0] in KEYWORDS or "(" in m.group(0).split("=")[0]:
            continue
        if m.group(4) or not (m.group(2) or WORD.match(words[-1])):
            break
        found[offset] = m.group(3)
        offset -= 4
    return found


def _units(raw: int) -> int:
    return raw if raw < 3 else ((raw - 2) >> 2) + 2


def _span(r) -> int:
    return len({row[0] for row in r.blocks} | set(r.default_blocks))


def _assignment_sites(source, function, x):
    begin, stop = _body(source, function)
    return [begin + m.end() for m in re.finditer(rf"^[^\n]*\b{re.escape(x)}\s*(?:[-+*/|&^]?=)(?!=)[^;\n]*;[^\n]*$",
                                                 source[begin:stop], re.M)]


CONSTANT = re.compile(r"^\(?\s*-?\s*(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*\s*\)?$")
IDENT = re.compile(r"^\(?\s*(?:\([A-Za-z_][\w \t*]*\)\s*)?[A-Za-z_]\w*\s*\)?$")


def _locals_and_params(source, function) -> set[str]:
    begin, stop = _body(source, function)
    head = source[source.rfind("\n", 0, source.rfind("{", 0, begin) if "{" in source[:begin] else begin) + 1:begin]
    params = set(re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[,)]", head))
    decls = set(re.findall(r"^[ \t]*(?:[A-Za-z_]\w*[ \t]+)+\**[ \t]*([A-Za-z_]\w*)[ \t]*(?:\[[^\]]*\])?[ \t]*[;=]",
                           source[begin:stop], re.M))
    return params | decls


def _read_counts(source, function, x, site) -> bool:
    """Whether an empty test right after the assignment ending at `site` is a read to uopt: not after a constant
    (folded: h6, H16b) or a copy of another local or parameter (propagated: H16b; var_a1 = arg1 in
    enqueueSoundEffectWithVolume). A global is a load, not a copy (H16: +1 after a load). H16b also lost the read
    inside a conditional arm for a computed value, but a real arm site performed (freeRelocatableHeapBlock,
    action_trace_v1.json), so arms are not gated."""
    line = source[source.rfind("\n", 0, site - 1) + 1:site]
    m = re.search(rf"\b{re.escape(x)}\s*=(?!=)\s*([^;]+);", line)
    if not m:
        return True
    value = m.group(1).strip()
    if CONSTANT.match(value):
        return False
    if IDENT.match(value):
        name = re.findall(r"[A-Za-z_]\w*", value)[-1]
        return name not in _locals_and_params(source, function)
    return True


def _inline(source, function, x):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    assigns = list(re.finditer(rf"^[ \t]*{re.escape(x)}\s*=(?!=)\s*(?P<e>[^;\n]+);[ \t]*\n", body, re.M))
    decl = re.search(rf"^[ \t]*(?:[A-Za-z_]\w*[ \t]+)+\**[ \t]*{re.escape(x)}[ \t]*;[ \t]*\n", body, re.M)
    if len(assigns) != 1 or not decl or re.search(r"\w+\s*\(", assigns[0].group("e")):
        return None
    e = assigns[0].group("e").strip()
    text = body[:assigns[0].start()] + body[assigns[0].end():]
    text = text.replace(decl.group(0), "", 1)
    text = re.sub(rf"\b{re.escape(x)}\b", lambda _m: f"({e})", text)
    return source[:begin] + text + source[stop:]


def _move_first(source, function, x, before):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    mx = re.search(rf"^[ \t]*{re.escape(x)}\s*=(?!=)[^;\n]*;[ \t]*\n", body, re.M)
    my = re.search(rf"^[ \t]*{re.escape(before)}\s*=(?!=)[^;\n]*;[ \t]*\n", body, re.M)
    if not mx or not my or mx.start() < my.start():
        return None
    text = body[:mx.start()] + body[mx.end():]
    return source[:begin] + text[:my.start()] + mx.group(0) + text[my.start():] + source[stop:]


def propose(source: str, function: str, report: dict, proc) -> list[tuple[str, str]]:
    """`(label, candidate)` for each wrong range the operators can address."""
    names = local_offsets(source, function)
    var = {lr: names.get(r.offset) for lr, r in proc.ranges.items() if r.kind == "M"}
    outcome = {d.piece: d.outcome for d in proc.decisions}
    order = [d.piece for d in proc.decisions if d.outcome != "not_colored"]
    from solver import uopt_attribution as ua
    holder_of = {}
    for lr in order:
        r = proc.ranges.get(lr)
        num = ua.colour_register(r.color) if r else None
        if num is not None:
            holder_of.setdefault(ua.REGISTER_NAMES[num], lr)
    out, seen = [], {source}

    def add(label, cand):
        if cand and cand not in seen and len(out) < CAP:
            seen.add(cand)
            out.append((label, cand))

    for w in report.get("ranges", []):
        if w.get("class") == "ok":
            continue
        x = var.get(w["lr"])
        if not x:
            continue
        if w["class"] == "ugen_temp":
            add(f"inline:{x}", _inline(source, function, x))
            continue
        y_lr = holder_of.get(w.get("desired"))
        if y_lr is None or y_lr == w["lr"] or y_lr not in order or w["lr"] not in order \
                or order.index(y_lr) > order.index(w["lr"]):
            continue                                   # only a range coloured AFTER the holder is raised
        rx, ry = proc.ranges[w["lr"]], proc.ranges[y_lr]
        # raise only where priority is the cause: `blocked` (the colour went to an interfering range coloured
        # first). A `selection` range had its colour free, so priority is not what chose another one
        # (eval/results/alloc-inverter-20260923/action_check.json: the raise moved its range in 2 of 30).
        if w["class"] == "blocked" and outcome.get(w["lr"]) == outcome.get(y_lr) == "constrained" \
                and rx.adjsave is not None and ry.adjsave is not None:
            ux, save = _units(_span(rx)), rx.adjsave * _units(_span(rx))
            k = next((k for k in range(1, 200) if (save + k) / ux > ry.adjsave
                      or ((save + k) / ux == ry.adjsave and w["lr"] < y_lr)), None)
            if k is None:
                continue
            for site in _assignment_sites(source, function, x):
                if not _read_counts(source, function, x, site):
                    continue
                for kk in sorted({1, max(1, (k + 9) // 10), k, k + 1, k + 2}):   # loop sites need ~k/10
                    add(f"raise:{x}+{kk}@{site}",
                        source[:site] + "\n" + "\n".join(f"    if (!{x});" for _ in range(kk)) + source[site:])
        elif outcome.get(w["lr"]) == outcome.get(y_lr) == "unconstrained":
            y = var.get(y_lr)
            if y:
                add(f"earlier:{x}<{y}", _move_first(source, function, x, y))
    return out

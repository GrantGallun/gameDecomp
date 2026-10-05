"""Generic, semantics-preserving, two-way C rewrites for rule mining (Engine A of solver.rule_miner).

Each rewrite turns one spelling into an equivalent one, in both directions. Nothing here knows about IDO. Which
spellings IDO compiles differently, and into what, is mined by compiling these rewrites on functions we already
match exactly (eval/rule_mine.py, the Ruler idea: enumerate, let the oracle validate, prune). At repair time the
same rewrites are applied to unsolved candidates, ordered by the mined rules.

A rewrite yields `(rule, label, new_source)`. `rule` names the rewrite and its direction (`for->rotated`), which is
the unit the miner learns about. The label also names the site. Every rewrite declines when equivalence would
depend on facts it can't see: side effects in a duplicated condition, `continue` in a rotated loop, statements that
touch the same names or memory.
"""
from __future__ import annotations

import re

from solver import c89

IDENT = r"[A-Za-z_]\w*"
LVALUE = rf"{IDENT}(?:(?:->|\.){IDENT}|\[[^\[\]]*\])*"
LIMIT = 3                                   # sites per rewrite per function
KEYWORDS = {"if", "else", "for", "while", "do", "return", "switch", "case", "default", "goto", "break", "continue",
            "sizeof"}


# ---------------------------------------------------------------------------------------------- structure

def _body(source: str, function: str):
    from solver import regalloc_mutations
    return regalloc_mutations._body(source, function)


def _match_close(masked: str, i: int, open_ch: str = "{", close_ch: str = "}") -> int | None:
    depth = 0
    for k in range(i, len(masked)):
        if masked[k] == open_ch:
            depth += 1
        elif masked[k] == close_ch:
            depth -= 1
            if depth == 0:
                return k
    return None


def statements(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    """Top-level statements of the block text masked[start:end], as absolute (start, end) spans.

    A compound statement (if/else chains, loops, do-while, bare blocks) is one statement. A label is its own
    statement, which keeps it a barrier for reordering.
    """
    out, k = [], start
    while k < end:
        while k < end and masked[k].isspace():
            k += 1
        if k >= end:
            break
        s = k
        m = re.match(rf"({IDENT})\s*:(?!:)", masked[k:end])
        if m and m.group(1) not in KEYWORDS and not masked[k:end].startswith("default"):
            out.append((s, k + m.end()))
            k += m.end()
            continue
        depth_p = 0
        while k < end:
            ch = masked[k]
            if ch == "(":
                depth_p += 1
            elif ch == ")":
                depth_p -= 1
            elif ch == ";" and depth_p == 0:
                k += 1
                break
            elif ch == "{" and depth_p == 0:
                close = _match_close(masked, k)
                if close is None:
                    return out
                k = close + 1
                rest = masked[k:end]
                nxt = re.match(r"\s*(else\b|while\b)", rest)
                if nxt and nxt.group(1) == "else":
                    k += nxt.end()
                    continue                 # the else arm belongs to this statement
                if nxt and nxt.group(1) == "while" and re.match(r"\s*do\b", masked[s:s + 6]):
                    semi = masked.find(";", k)
                    k = semi + 1 if semi != -1 else end
                break
            k += 1
        out.append((s, k))
    return out


def blocks(masked: str, begin: int, stop: int):
    """(start, end) of every `{...}` block interior in [begin, stop), the function body included."""
    yield begin, stop
    k = begin
    while k < stop:
        if masked[k] == "{":
            close = _match_close(masked, k)
            if close is None or close > stop:
                return
            yield k + 1, close
        k += 1


def _idents(text: str) -> set[str]:
    return {t for t in re.findall(IDENT, text) if t not in KEYWORDS}


def _has_call(text: str) -> bool:
    return bool(re.search(rf"(?<![\w.>]){IDENT}\s*\(", re.sub(r"\b(?:if|while|for|switch|sizeof|return)\s*\(", "", text)))


def _side_effects(text: str) -> bool:
    return _has_call(text) or bool(re.search(r"\+\+|--|(?<![=!<>])=(?!=)", text))


def _replace(source: str, start: int, end: int, text: str) -> str:
    return source[:start] + text + source[end:]


# ---------------------------------------------------------------------------------------------- rewrites

def increment_forms(source, function):
    """`x++;` <-> `x += 1;` <-> `x = x + 1;` (and decrements)."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    forms = {"pp": "{x}{op}{op};", "pe": "{x} {op}= 1;", "ae": "{x} = {x} {op} 1;"}
    pat = re.compile(rf"(?<![\w.>])(?P<x>{LVALUE})\s*(?:(?P<pp>\+\+|--)|(?P<pe>[+-])=\s*1\b|=\s*(?P=x)\s*(?P<ae>[+-])\s*1\b)\s*;")
    out, n = [], 0
    for m in pat.finditer(masked, begin, stop):
        kind = "pp" if m["pp"] else "pe" if m["pe"] else "ae"
        op = m["pp"][0] if m["pp"] else (m["pe"] or m["ae"])
        x = source[m.start("x"):m.end("x")]
        for other, fmt in forms.items():
            if other != kind:
                out.append((f"inc:{kind}->{other}", f"inc {x} {kind}->{other}",
                            _replace(source, m.start(), m.end(), fmt.format(x=x, op=op))))
        n += 1
        if n >= LIMIT:
            break
    return out


def compound_assign(source, function):
    """`x = x OP e;` <-> `x OP= e;`."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    ops = r"<<|>>|[-+*&|^]"
    out, n = [], 0
    for m in re.finditer(rf"(?<![\w.>])(?P<x>{LVALUE})\s*=\s*(?P=x)\s*(?P<op>{ops})\s*(?P<e>[^;]+);", masked[:stop]):
        if m.start() < begin or m["e"].strip() == "1":
            continue
        e = source[m.start("e"):m.end("e")].strip()
        x = source[m.start("x"):m.end("x")]
        out.append(("compound:long->short", f"compound {x}", _replace(source, m.start(), m.end(), f"{x} {m['op']}= {e};")))
        n += 1
        if n >= LIMIT:
            break
    n = 0
    for m in re.finditer(rf"(?<![\w.>])(?P<x>{LVALUE})\s*(?P<op><<|>>|[-+*&|^])=\s*(?P<e>[^;]+);", masked[:stop]):
        if m.start() < begin or m["e"].strip() == "1":
            continue
        e = source[m.start("e"):m.end("e")].strip()
        x = source[m.start("x"):m.end("x")]
        out.append(("compound:short->long", f"compound {x}",
                    _replace(source, m.start(), m.end(), f"{x} = {x} {m['op']} ({e});")))
        n += 1
        if n >= LIMIT:
            break
    return out


def counted_loop_rotation(source, function):
    """`for (i = 0; i < n; i++) { body }` <-> m2c's `i = 0; if (n > 0) { for (;;) { body i += 1; if (!(i < n)) break; } }`."""
    from solver import counted_loop
    out = [("loop:rotated->for", label, cand) for label, cand in counted_loop.variants(source, function)][:LIMIT]
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    head = re.compile(rf"for\s*\(\s*(?P<i>{IDENT})\s*=\s*0\s*;\s*(?P=i)\s*<\s*(?P<n>[^;]+?)\s*;\s*"
                      rf"(?:(?P=i)\s*\+\+|\+\+\s*(?P=i)|(?P=i)\s*\+=\s*1)\s*\)\s*\{{")
    for m in head.finditer(masked, begin, stop):
        close = _match_close(masked, m.end() - 1)
        if close is None or close > stop:
            continue
        inner = source[m.end():close]
        if re.search(r"\bcontinue\b", c89._mask(inner)) or _side_effects(source[m.start("n"):m.end("n")]):
            continue
        i, n = m["i"], source[m.start("n"):m.end("n")].strip()
        line = source.rfind("\n", 0, m.start()) + 1
        ind = re.match(r"[ \t]*", source[line:]).group(0)
        body = "".join(f"{ind}        {ln.strip()}\n" for ln in inner.strip("\n").splitlines() if ln.strip())
        new = (f"{i} = 0;\n{ind}if ({n} > 0) {{\n{ind}    for (;;) {{\n{body}{ind}        {i} += 1;\n"
               f"{ind}        if (!({i} < {n})) break;\n{ind}    }}\n{ind}}}")
        out.append(("loop:for->rotated", f"rotate for {i}", _replace(source, m.start(), close + 1, new)))
        if len(out) >= 2 * LIMIT:
            break
    return out


def copyback_temporary(source, function):
    """`x = e;` <-> `t = e; x = t;` (m2c's copy-back temporary)."""
    from solver import temp_copyback
    out = [("temp:copyback->direct", label, cand)
           for label, cand in temp_copyback.variants(source, function, "", gated=False)][:LIMIT]
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    decls = {m["name"]: m["type"] for m in re.finditer(
        rf"^[ \t]*(?P<type>(?:(?:unsigned|signed)\s+)?{IDENT})\s+(?P<name>{IDENT})\s*;", masked[begin:stop], re.M)
        if m["type"] not in KEYWORDS}
    n = 0
    for s, e in statements(masked, begin, stop):
        m = re.match(rf"(?P<x>{IDENT})\s*=\s*(?P<e>[^;=][^;]*);$", masked[s:e])
        if not m or m["x"] not in decls or decls[m["x"]] in ("struct", "union"):
            continue
        t = f"temp_rw{n}"
        expr = source[s + m.start("e"):s + m.end("e")].strip()
        line = source.rfind("\n", 0, s) + 1
        ind = re.match(r"[ \t]*", source[line:]).group(0)
        new = _replace(source, s, e, f"{t} = {expr};\n{ind}{m['x']} = {t};")
        first_decl = re.search(r"^[ \t]*\S", source[begin:], re.M)
        at = begin + (first_decl.start() if first_decl else 0)
        new = new[:at] + f"    {decls[m['x']]} {t};\n" + new[at:]
        out.append(("temp:direct->copyback", f"copyback {m['x']}", new))
        n += 1
        if n >= LIMIT:
            break
    return out


def _if_else(masked: str, begin: int, stop: int):
    """(if_start, cond_open, cond_close, then_open, then_close, else_open, else_close) for simple if/else pairs."""
    for m in re.finditer(r"\bif\s*\(", masked[begin:stop]):
        s = begin + m.start()
        if re.search(r"\belse\s*$", masked[max(begin, s - 12):s]):
            continue                          # part of an else-if chain
        co = begin + m.end() - 1
        cc = _match_close(masked, co, "(", ")")
        if cc is None:
            continue
        tm = re.match(r"\s*\{", masked[cc + 1:])
        if not tm:
            continue
        to = cc + 1 + tm.end() - 1
        tc = _match_close(masked, to)
        if tc is None:
            continue
        em = re.match(r"\s*else\s*\{", masked[tc + 1:])
        if not em:
            continue
        eo = tc + 1 + em.end() - 1
        ec = _match_close(masked, eo)
        if ec is None or ec > stop:
            continue
        yield s, co, cc, to, tc, eo, ec


def _negate(cond: str) -> str:
    c = cond.strip()
    m = re.fullmatch(r"!\s*\((.*)\)", c, re.S)
    if m and _match_close(m.group(0), m.group(0).index("("), "(", ")") == len(m.group(0)) - 1:
        return m.group(1).strip()
    return f"!({c})"


def empty_arm_drops(source, function):
    """`if (c) {} else {B}` -> `if (!(c)) {B}`; `if (c) {A} else {}` -> `if (c) {A}`.

    m2c and `if_arm_swap` both leave empty arms, and IDO does not compile an empty arm away: `if (!(x == 0)) {}
    else {B}` branches differently from `if (x == 0) {B}` (eval/results/edit-capability-20261002, planted
    if_invert on updateRacePlayerRecoverySparkle; `if_arm_swap` alone yields `if (c) {B} else {}`).
    """
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    out = []
    for s, co, cc, to, tc, eo, ec in _if_else(masked, begin, stop):
        cond = source[co + 1:cc]
        then_empty, else_empty = not masked[to + 1:tc].strip(), not masked[eo + 1:ec].strip()
        if then_empty and not else_empty:
            new = source[:co + 1] + _negate(cond) + source[cc:to + 1] + source[eo + 1:ec] + source[tc:tc + 1] + source[ec + 1:]
            out.append(("empty_arm:then", "drop empty then-arm", new))
        elif else_empty and not then_empty:
            new = source[:tc + 1] + source[ec + 1:]
            out.append(("empty_arm:else", "drop empty else-arm", new))
        if len(out) >= LIMIT:
            break
    return out


def if_arm_swap(source, function):
    """`if (c) {A} else {B}` <-> `if (!(c)) {B} else {A}` (one rewrite; applied twice it is the identity)."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    out = []
    for s, co, cc, to, tc, eo, ec in _if_else(masked, begin, stop):
        cond = source[co + 1:cc]
        new = (source[:co + 1] + _negate(cond) + source[cc:to + 1] + source[eo + 1:ec] + source[tc:eo + 1]
               + source[to + 1:tc] + source[ec:])
        kind = "negated->plain" if cond.strip().startswith("!") else "plain->negated"
        out.append((f"ifswap:{kind}", "swap if/else arms", new))
        if len(out) >= LIMIT:
            break
    return out


def truth_test(source, function):
    """`if (e != 0)` <-> `if (e)`, `if (e == 0)` <-> `if (!e)`, in if/while conditions."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    out = []
    for m in re.finditer(r"\b(if|while)\s*\(", masked[begin:stop]):
        co = begin + m.end() - 1
        cc = _match_close(masked, co, "(", ")")
        if cc is None:
            continue
        cond = source[co + 1:cc].strip()
        mc = re.fullmatch(r"(.+?)\s*(!=|==)\s*0", cond, re.S)
        if mc and "&&" not in cond and "||" not in cond:
            e = mc.group(1).strip()
            new_cond = e if mc.group(2) == "!=" else f"!({e})" if not re.fullmatch(LVALUE, e) else f"!{e}"
            out.append((f"truth:explicit->implicit", "truth test", _replace(source, co + 1, cc, new_cond)))
        elif "&&" not in cond and "||" not in cond and not re.search(r"[=<>!]=|[<>]", cond):
            # Always parenthesise the operand: `a & 4 != 0` parses as `a & (4 != 0)` and changes meaning (the
            # first mining run billed that as a 55% "effect", rule-miner-20260929).
            if cond.startswith("!"):
                inner = cond[1:].strip()
                out.append(("truth:implicit->explicit", "truth test", _replace(source, co + 1, cc, f"({inner}) == 0")))
            else:
                out.append(("truth:implicit->explicit", "truth test", _replace(source, co + 1, cc, f"({cond}) != 0")))
        if len(out) >= LIMIT:
            break
    return out


def ternary_if(source, function):
    """`x = c ? a : b;` <-> `if (c) { x = a; } else { x = b; }`."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    out = []
    for blk_s, blk_e in blocks(masked, begin, stop):
        for s, e in statements(masked, blk_s, blk_e):
            text = masked[s:e]
            m = re.match(rf"(?P<x>{LVALUE})\s*=\s*(?P<rhs>[^;]+);$", text)
            if not m or m["x"].split("(")[0] in KEYWORDS:
                continue
            rhs_s = s + m.start("rhs")
            depth, q, col = 0, None, None
            for k, ch in enumerate(masked[rhs_s:s + m.end("rhs")]):
                if ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "?" and depth == 0 and q is None:
                    q = k
                elif ch == ":" and depth == 0 and q is not None:
                    col = k
                    break
            if q is None or col is None:
                continue
            r = source[rhs_s:s + m.end("rhs")]
            c, a, b = r[:q].strip(), r[q + 1:col].strip(), r[col + 1:].strip()
            x = source[s:s + m.end("x")]
            line = source.rfind("\n", 0, s) + 1
            ind = re.match(r"[ \t]*", source[line:]).group(0)
            new = f"if ({c}) {{\n{ind}    {x} = {a};\n{ind}}} else {{\n{ind}    {x} = {b};\n{ind}}}"
            out.append(("ternary:ternary->if", f"ternary {x}", _replace(source, s, e, new)))
    for s, co, cc, to, tc, eo, ec in _if_else(masked, begin, stop):
        ta, ea = masked[to + 1:tc].strip(), masked[eo + 1:ec].strip()
        mt = re.fullmatch(rf"(?P<x>{LVALUE})\s*=\s*([^;]+);", ta)
        me = re.fullmatch(rf"(?P<x>{LVALUE})\s*=\s*([^;]+);", ea)
        if mt and me and mt["x"] == me["x"]:
            c = source[co + 1:cc].strip()
            a = source[to + 1:tc].strip().split("=", 1)[1].rstrip(";").strip()
            b = source[eo + 1:ec].strip().split("=", 1)[1].rstrip(";").strip()
            out.append(("ternary:if->ternary", f"ternary {mt['x']}", _replace(source, s, ec + 1, f"{mt['x']} = {c} ? {a} : {b};")))
    return out[:2 * LIMIT]


def index_pointer(source, function):
    """`p[i]` <-> `*(p + i)` for a plain identifier p."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    out = []
    for m in re.finditer(rf"(?<![\w.>\]])(?P<p>{IDENT})\s*\[(?P<i>[^\[\]]+)\]", masked[begin:stop]):
        s, e = begin + m.start(), begin + m.end()
        line_start = masked.rfind("\n", 0, s) + 1
        decl = re.match(rf"\s*(?:(?:unsigned|signed|struct|union)\s+)?({IDENT})[\s\*]+{m['p']}\s*\[", masked[line_start:e])
        if decl and decl.group(1) not in KEYWORDS:
            continue                          # a declaration (`return p[i]` is not one)
        i = source[begin + m.start("i"):begin + m.end("i")].strip()
        # Parenthesised as a whole: `p[i].x` -> `*(p + i).x` broke 32% of compiles in the first mining run.
        out.append(("index:subscript->pointer", f"index {m['p']}", _replace(source, s, e, f"(*({m['p']} + {i}))")))
        if len(out) >= LIMIT:
            break
    n = 0
    for m in re.finditer(rf"\*\s*\(\s*(?P<p>{IDENT})\s*\+\s*(?P<i>[^()]+?)\s*\)", masked[begin:stop]):
        s, e = begin + m.start(), begin + m.end()
        out.append(("index:pointer->subscript", f"index {m['p']}", _replace(source, s, e, f"{m['p']}[{m['i'].strip()}]")))
        n += 1
        if n >= LIMIT:
            break
    return out


def _reads_writes(text: str):
    m = re.match(rf"\s*(?P<lhs>{LVALUE})\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)", text)
    writes = set()
    if m:
        writes = {re.match(IDENT, m["lhs"]).group(0)}
    for mm in re.finditer(rf"({IDENT})\s*(?:\+\+|--)|(?:\+\+|--)\s*({IDENT})", text):
        writes.add(mm.group(1) or mm.group(2))
    return _idents(text), writes


def statement_swap(source, function):
    """Swap two adjacent simple statements that share no written name and touch no common memory."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    out = []
    for blk_s, blk_e in blocks(masked, begin, stop):
        spans = statements(masked, blk_s, blk_e)
        for (a_s, a_e), (b_s, b_e) in zip(spans, spans[1:]):
            a, b = masked[a_s:a_e], masked[b_s:b_e]
            if not (a.endswith(";") and b.endswith(";")) or any(x in a + b for x in ("{", "return", "goto", "break")):
                continue
            if re.match(rf"\s*{IDENT}\s*:", a) or re.match(rf"\s*{IDENT}\s*:", b):
                continue
            if re.match(rf"^\s*(?:(?:unsigned|signed|struct|union|const)\s+)*{IDENT}[\s\*]+{IDENT}\s*(?:\[[^\]]*\])?\s*;", a):
                continue                      # declarations
            ra, wa = _reads_writes(a)
            rb, wb = _reads_writes(b)
            if (not wa and not _has_call(a)) or (not wb and not _has_call(b)):
                continue
            if wa & (rb | wb) or wb & ra:
                continue
            mem = lambda t: bool(re.search(r"->|\*|\[", t))
            if (_has_call(a) or _has_call(b)) and (_has_call(a) and _has_call(b) or mem(a) or mem(b) or wa or wb):
                continue
            if mem(a.split("=")[0]) and mem(b) or mem(b.split("=")[0]) and mem(a):
                continue                      # a store through memory next to another memory access may alias
            new = source[:a_s] + source[b_s:b_e] + source[a_e:b_s] + source[a_s:a_e] + source[b_e:]
            out.append(("swap:adjacent", "swap statements", new))
            if len(out) >= LIMIT:
                return out
    return out


def while_rotation(source, function):
    """`while (c) {B}` <-> `if (c) { do {B} while (c); }` when c has no side effects."""
    begin, stop = _body(source, function)
    masked = c89._mask(source)
    out = []
    for m in re.finditer(r"\bwhile\s*\(", masked[begin:stop]):
        s = begin + m.start()
        if re.search(r"\}\s*$", masked[max(begin, s - 4):s]):
            continue                          # the tail of a do-while
        co = begin + m.end() - 1
        cc = _match_close(masked, co, "(", ")")
        if cc is None:
            continue
        bm = re.match(r"\s*\{", masked[cc + 1:])
        if not bm:
            continue
        bo = cc + 1 + bm.end() - 1
        bc = _match_close(masked, bo)
        if bc is None or bc > stop:
            continue
        cond = source[co + 1:cc]
        if _side_effects(masked[co + 1:cc]) or re.search(r"\bcontinue\b", masked[bo:bc]):
            continue
        new = f"if ({cond.strip()}) {{\n    do {source[bo:bc + 1]} while ({cond.strip()});\n}}"
        out.append(("while:while->ifdo", "rotate while", _replace(source, s, bc + 1, new)))
        if len(out) >= LIMIT:
            break
    for m in re.finditer(r"\bif\s*\(", masked[begin:stop]):
        s = begin + m.start()
        co = begin + m.end() - 1
        cc = _match_close(masked, co, "(", ")")
        if cc is None:
            continue
        dm = re.match(r"\s*\{\s*do\s*\{", masked[cc + 1:])
        if not dm:
            continue
        do_open = cc + 1 + dm.end() - 1
        do_close = _match_close(masked, do_open)
        if do_close is None:
            continue
        wm = re.match(r"\s*while\s*\(", masked[do_close + 1:])
        if not wm:
            continue
        wo = do_close + 1 + wm.end() - 1
        wc = _match_close(masked, wo, "(", ")")
        tail = re.match(r"\s*;\s*\}", masked[wc + 1:]) if wc else None
        if not tail or masked[co + 1:cc].strip() != masked[wo + 1:wc].strip():
            continue
        new = f"while ({source[co + 1:cc].strip()}) {source[do_open:do_close + 1]}"
        out.append(("while:ifdo->while", "unrotate while", _replace(source, s, wc + 1 + tail.end(), new)))
        if len(out) >= 2 * LIMIT:
            break
    return out


REWRITES = (increment_forms, compound_assign, counted_loop_rotation, copyback_temporary, if_arm_swap, truth_test,
            ternary_if, index_pointer, statement_swap, while_rotation)


def all_variants(source: str, function: str, limit: int | None = None):
    """Every rewrite's variants: [(rule, label, new_source)], with generator failures skipped, not raised.

    `limit` raises the per-rewrite site cap; with residual localisation (solver.rule_miner) more sites are worth
    generating, because the matcher then prefers the ones on residual lines."""
    global LIMIT
    saved = LIMIT
    if limit is not None:
        LIMIT = limit
    try:
        return _all_variants(source, function)
    finally:
        LIMIT = saved


def _all_variants(source: str, function: str):
    out = []
    for rewrite in REWRITES:
        try:
            for rule, label, new in rewrite(source, function):
                if new != source:
                    out.append((rule, label, new))
        except Exception:
            continue
    return out

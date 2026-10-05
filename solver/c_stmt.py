"""Statement trees for one function body: parse, transform, render.

Expressions stay as source text (solver.term_rewrite parses them when a transform needs to). Comments are dropped;
declarations, labels, case labels and ordinary statements are kept verbatim. Rendering is m2c-style indentation.
IDO compiles from the syntax tree, so layout and dropped comments cannot change the object; transforms that change
structure are judged by the compiler like any other candidate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from solver import c89
from solver.regalloc_mutations import TYPE_WORDS, Decline, _typedefs, tokens


@dataclass
class S:
    kind: str                       # block if while do for switch label case default goto return break continue
    text: str = ""                  # expr / decl / empty; condition; label name; return value; case value
    body: list = field(default_factory=list)     # block items, or [then] / [body]
    orelse: "S | None" = None
    init: str = ""
    step: str = ""

    def __repr__(self):
        return render([self]).strip()


class _P:
    def __init__(self, source: str, masked: str, toks, typedefs):
        self.src, self.masked, self.toks, self.i, self.typedefs = source, masked, toks, 0, typedefs

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else ("eof", "", len(self.src), len(self.src))

    def take(self, value=None):
        t = self.peek()
        if t[0] == "eof" or (value is not None and t[1] != value):
            raise Decline(f"expected {value!r}, found {t[1]!r}")
        self.i += 1
        return t

    def text(self, a, b) -> str:
        return " ".join(self.src[a:b].split())

    def until(self, stops: set, depth_open="([{", depth_close=")]}"):
        """Consume tokens up to (not including) a top-level stop token; returns the source span."""
        start = self.peek()[2]
        depth = 0
        end = start
        while True:
            t = self.peek()
            if t[0] == "eof":
                raise Decline("unterminated")
            if depth == 0 and t[1] in stops:
                return start, end
            if t[1] in depth_open:
                depth += 1
            elif t[1] in depth_close:
                depth -= 1
                if depth < 0:
                    return start, end
            end = t[3]
            self.i += 1

    def paren(self) -> str:
        self.take("(")
        a, b = self.until({")"})
        self.take(")")
        return self.text(a, b)

    def block(self) -> S:
        self.take("{")
        items = []
        while self.peek()[1] != "}":
            items.append(self.statement())
        self.take("}")
        return S("block", body=items)

    def statement(self) -> S:
        kind, v, start, _e = self.peek()
        if v == "{":
            return self.block()
        if v == ";":
            self.take()
            return S("block", body=[])
        if v == "if":
            self.take()
            cond = self.paren()
            then = self.statement()
            orelse = None
            if self.peek()[1] == "else":
                self.take()
                orelse = self.statement()
            return S("if", cond, [then], orelse)
        if v == "while":
            self.take()
            cond = self.paren()
            return S("while", cond, [self.statement()])
        if v == "do":
            self.take()
            body = self.statement()
            self.take("while")
            cond = self.paren()
            self.take(";")
            return S("do", cond, [body])
        if v == "for":
            self.take()
            self.take("(")
            a, b = self.until({";"})
            init = self.text(a, b)
            self.take(";")
            a, b = self.until({";"})
            cond = self.text(a, b)
            self.take(";")
            a, b = self.until({")"})
            step = self.text(a, b)
            self.take(")")
            return S("for", cond, [self.statement()], init=init, step=step)
        if v == "switch":
            self.take()
            cond = self.paren()
            return S("switch", cond, [self.statement()])
        if v == "case":
            self.take()
            a, b = self.until({":"})
            self.take(":")
            return S("case", self.text(a, b))
        if v == "default" and self.peek(1)[1] == ":":
            self.take()
            self.take(":")
            return S("default")
        if v == "goto":
            self.take()
            name = self.take()[1]
            self.take(";")
            return S("goto", name)
        if v == "return":
            self.take()
            a, b = self.until({";"})
            self.take(";")
            return S("return", self.text(a, b))
        if v in ("break", "continue"):
            self.take()
            self.take(";")
            return S(v)
        if kind == "ident" and self.peek(1)[1] == ":" and self.peek(2)[1] != ":":
            self.take()
            self.take(":")
            return S("label", v)
        a, b = self.until({";"})
        self.take(";")
        text = self.text(a, b) + ";"
        is_decl = kind == "ident" and (v in TYPE_WORDS or v in self.typedefs) and self.peek(-1)[0] != "eof"
        return S("decl" if is_decl and _looks_decl(text, self.typedefs) else "expr", text)


def _looks_decl(text: str, typedefs) -> bool:
    toks = text.replace("*", " * ").split()
    return len(toks) >= 2 and (toks[0] in TYPE_WORDS or toks[0] in typedefs) and toks[1] not in ("=", "(", "[")


def parse_body(source: str, begin: int, stop: int) -> S:
    """The block between a function's braces (begin/stop from rewrite_library._body: inside the braces)."""
    masked = c89._mask(source)
    toks = tokens(masked[begin:stop], begin)
    p = _P(source, masked, [("op", "{", begin - 1, begin)] + toks + [("op", "}", stop, stop + 1)], _typedefs(source))
    b = p.block()
    if p.i != len(p.toks):
        raise Decline("trailing tokens after body")
    return b


def render(items: list, indent: int = 1) -> str:
    pad = "    " * indent
    out = []
    for s in items:
        k = s.kind
        if k == "block":
            if s.body:
                out.append(pad + "{\n" + render(s.body, indent + 1) + pad + "}\n")
        elif k in ("expr", "decl"):
            out.append(pad + s.text + "\n")
        elif k == "if":
            out.append(pad + f"if ({s.text}) " + _braced(s.body[0], indent))
            e = s.orelse
            while e is not None:
                if e.kind == "if":
                    out[-1] = out[-1].rstrip("\n") + f" else if ({e.text}) " + _braced(e.body[0], indent)
                    e = e.orelse
                else:
                    out[-1] = out[-1].rstrip("\n") + " else " + _braced(e, indent)
                    e = None
        elif k == "while":
            out.append(pad + f"while ({s.text}) " + _braced(s.body[0], indent))
        elif k == "do":
            out.append(pad + "do " + _braced(s.body[0], indent).rstrip("\n") + f" while ({s.text});\n")
        elif k == "for":
            out.append(pad + f"for ({s.init}; {s.text}; {s.step}) " + _braced(s.body[0], indent))
        elif k == "switch":
            out.append(pad + f"switch ({s.text}) " + _braced(s.body[0], indent))
        elif k == "label":
            out.append(f"{s.text}:" + (";\n" if s is items[-1] else "\n"))    # C needs a statement after a label
        elif k == "case":
            out.append(pad[:-4] + f"    case {s.text}:\n")
        elif k == "default":
            out.append(pad[:-4] + "    default:\n")
        elif k == "goto":
            out.append(pad + f"goto {s.text};\n")
        elif k == "return":
            out.append(pad + (f"return {s.text};\n" if s.text else "return;\n"))
        elif k in ("break", "continue"):
            out.append(pad + k + ";\n")
        else:
            raise ValueError(k)
    return "".join(out)


def _braced(s: S, indent: int) -> str:
    items = s.body if s.kind == "block" else [s]
    return "{\n" + render(items, indent + 1) + "    " * indent + "}\n"


def replace_body(source: str, begin: int, stop: int, block: S) -> str:
    return source[:begin] + "\n" + render(block.body) + source[stop:]


# ---------------------------------------------------------------------------------------------- helpers

def items(s: S) -> list:
    """Statement list of a compound or single statement."""
    return s.body if s.kind == "block" else [s]


def walk(s: S):
    yield s
    for c in s.body:
        yield from walk(c)
    if s.orelse is not None:
        yield from walk(s.orelse)


def contains(s: S, pred, into_loops: bool = True) -> bool:
    if pred(s):
        return True
    if not into_loops and s.kind in ("while", "do", "for", "switch"):
        return False
    return any(contains(c, pred, into_loops) for c in s.body) or (s.orelse is not None and contains(s.orelse, pred, into_loops))


def same(a: list, b: list) -> bool:
    return render(a) == render(b)


def negate(cond: str) -> str:
    """The negated condition, spelled the way a programmer would (`a < b` -> `a >= b`, `!x` -> `x`)."""
    from solver import term_rewrite as tr
    flip = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
    try:
        p = tr.TreeParser(tokens(cond), set())
        root = p.assignment()
        if p.peek()[0] != "eof":
            raise Decline("trailing")
    except Decline:
        return f"!({cond})"
    n = root.strip()
    if n.kind == "bin" and n.op in flip:
        a, b = n.kids
        return f"{cond[a.start:a.end]} {flip[n.op]} {cond[b.start:b.end]}"
    if n.kind == "un" and n.op == "!":
        inner = n.kids[0].strip()
        return cond[inner.start:inner.end]
    if n.prec >= tr.PREFIX:
        return "!" + cond[n.start:n.end]
    return f"!({cond[n.start:n.end]})"

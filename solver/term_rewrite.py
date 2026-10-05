"""Expression-tree rewrites: parse C expressions in a function body, match term patterns, replace them safely.

A rule is a pair of term patterns over placeholders, e.g. `E0 * 2` <-> `E0 << 1`. `E<i>` binds any side-effect-free
subexpression (the same text wherever it repeats), `N<i>` binds an integer literal, and other literals match by value.
Matching is on the parse tree, not tokens, so `a * b + c` never yields a match of `E0 + E1` on `b + c`, and a
replacement is parenthesised wherever its precedence needs it.

eval/rewrite_enum.py generates the rules (enumeration + a semantic check + IDO probes); solver/rule_miner.py ranks and
applies them. The compiler still decides: a rule only proposes a spelling.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from solver import c89
from solver.regalloc_mutations import BINARY, KEYWORDS, TYPE_WORDS, Decline, _typedefs, tokens

INT_CASTS = ("u8", "s8", "u16", "s16", "u32", "s32")
UNARY = ("-", "~", "!")
PRIMARY, POSTFIX, PREFIX, COND, ASSIGN_P = 16, 15, 14, 0.5, 0.2
_ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
_PLACEHOLDER = re.compile(r"^([EN])(\d+)$")


@dataclass
class Node:
    kind: str                       # leaf bin un cast cond assign call index member post pre paren
    op: str = ""
    kids: list = field(default_factory=list)
    start: int = -1
    end: int = -1
    text: str = ""                  # leaf text, cast type, member name
    need: float = 0.0               # precedence its position requires (set by annotate); 0 = any expression

    @property
    def prec(self) -> float:
        if self.kind in ("leaf", "paren"):
            return PRIMARY
        if self.kind in ("call", "index", "member", "post"):
            return POSTFIX
        if self.kind in ("un", "cast", "pre"):
            return PREFIX
        if self.kind == "bin":
            return BINARY[self.op]
        return COND if self.kind == "cond" else ASSIGN_P

    def strip(self) -> "Node":
        n = self
        while n.kind == "paren":
            n = n.kids[0]
        return n


class TreeParser:
    """Precedence climbing over regalloc_mutations.tokens; builds Nodes with source spans."""

    def __init__(self, toks, typedefs):
        self.toks, self.i, self.typedefs = toks, 0, typedefs

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else ("eof", "", -1, -1)

    def take(self, value=None):
        t = self.peek()
        if value is not None and t[1] != value:
            raise Decline(f"expected {value!r}, found {t[1]!r}")
        if t[0] == "eof":
            raise Decline("end of input")
        self.i += 1
        return t

    def is_type_start(self, k):
        kind, value, *_ = self.peek(k)
        return kind == "ident" and (value in TYPE_WORDS or value in self.typedefs)

    def expression(self):
        n = self.assignment()
        if self.peek()[1] == ",":
            raise Decline("comma expression")
        return n

    def assignment(self):
        n = self.conditional()
        if self.peek()[1] in _ASSIGN and self.peek()[0] == "op":
            op = self.take()[1]
            r = self.assignment()
            return Node("assign", op, [n, r], n.start, r.end)
        return n

    def conditional(self):
        n = self.binary(1)
        if self.peek()[1] == "?":
            self.take()
            a = self.assignment()
            self.take(":")
            b = self.conditional()
            return Node("cond", "?", [n, a, b], n.start, b.end)
        return n

    def binary(self, minimum):
        left = self.unary()
        while True:
            kind, op, *_ = self.peek()
            p = BINARY.get(op)
            if p is None or p < minimum or kind != "op":
                return left
            self.take()
            right = self.binary(p + 1)
            left = Node("bin", op, [left, right], left.start, right.end)

    def unary(self):
        kind, value, start, _e = self.peek()
        if value in ("-", "+", "!", "~", "*", "&") and kind == "op":
            self.take()
            x = self.unary()
            return Node("un", value, [x], start, x.end)
        if value in ("++", "--"):
            self.take()
            x = self.unary()
            return Node("pre", value, [x], start, x.end)
        if value == "sizeof":
            raise Decline("sizeof")
        if value == "(" and self.is_type_start(1):
            self.take("(")
            words = []
            while self.peek()[1] != ")":
                t = self.take()
                if t[1] in (";", "{", "}", "("):
                    raise Decline("not a cast")
                words.append(t[1])
            self.take(")")
            x = self.unary()
            return Node("cast", "", [x], start, x.end, " ".join(words))
        return self.postfix()

    def postfix(self):
        n = self.primary()
        while True:
            value = self.peek()[1]
            if value == "(":
                self.take()
                args = []
                if self.peek()[1] != ")":
                    args.append(self.assignment())
                    while self.peek()[1] == ",":
                        self.take()
                        args.append(self.assignment())
                end = self.take(")")[3]
                n = Node("call", "", [n] + args, n.start, end)
            elif value == "[":
                self.take()
                ix = self.assignment()
                end = self.take("]")[3]
                n = Node("index", "", [n, ix], n.start, end)
            elif value in (".", "->"):
                self.take()
                t = self.take()
                if t[0] != "ident":
                    raise Decline("member name expected")
                n = Node("member", value, [n], n.start, t[3], t[1])
            elif value in ("++", "--"):
                end = self.take()[3]
                n = Node("post", value, [n], n.start, end)
            else:
                return n

    def primary(self):
        kind, value, start, end = self.take()
        if kind in ("ident", "number", "string") and value not in KEYWORDS | TYPE_WORDS:
            return Node("leaf", "", [], start, end, value)
        if value == "(":
            inner = self.assignment()
            if self.peek()[1] == ",":
                raise Decline("comma expression")
            end = self.take(")")[3]
            return Node("paren", "", [inner], start, end)
        raise Decline(f"unexpected token {value!r}")


# ---------------------------------------------------------------------------------------------- source trees

def walk(node: Node):
    yield node
    for k in node.kids:
        yield from walk(k)


def annotate(n: Node, need: float = 0.0) -> Node:
    """Record on every node the precedence its position requires, so a replacement is grouped only when needed."""
    n.need = need
    k = n.kids
    if n.kind == "bin":
        annotate(k[0], BINARY[n.op])
        annotate(k[1], BINARY[n.op] + 1)
    elif n.kind in ("un", "cast", "pre"):
        annotate(k[0], PREFIX)
    elif n.kind == "cond":
        annotate(k[0], 1)
        annotate(k[1], 0)
        annotate(k[2], COND)
    elif n.kind == "assign":
        annotate(k[0], PREFIX)
        annotate(k[1], ASSIGN_P)
    elif n.kind in ("member", "post"):
        annotate(k[0], POSTFIX)
    elif n.kind in ("call", "index"):
        annotate(k[0], POSTFIX)
        for x in k[1:]:
            annotate(x, ASSIGN_P)
    elif n.kind == "paren":
        annotate(k[0], 0)
    return n


def body_trees(source: str, begin: int, stop: int, typedefs=None) -> list[Node]:
    """Maximal expression trees in source[begin:stop], found by parsing from each statement/header boundary."""
    typedefs = _typedefs(source) if typedefs is None else typedefs
    masked = c89._mask(source)
    toks = tokens(masked[begin:stop], begin)
    out, i = [], 0
    starts = {"(", ";", "{", "}", "return", "else", ":", "do", "=", ","}
    covered = -1
    while i < len(toks):
        prev = toks[i - 1][1] if i else ";"
        if toks[i][3] <= covered or prev not in starts or toks[i][1] in KEYWORDS:
            i += 1
            continue
        p = TreeParser(toks[i:], typedefs)
        try:
            n = p.assignment()
        except Decline:
            i += 1
            continue
        nxt = p.peek()[1]
        if nxt in (";", ")", ",", ":", "eof", ""):
            out.append(annotate(n))
            covered = n.end
            i += max(p.i, 1)
        else:
            i += 1
    return out


def side_effect_free(node: Node) -> bool:
    return not any(n.kind in ("call", "assign", "pre", "post") for n in walk(node))


def _int(text: str):
    try:
        return int(text.rstrip("uUlL"), 0)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- patterns

def pattern(text: str) -> Node:
    """Parse a pattern such as `(u8)E0 - N0`; placeholders are identifiers E<i>/N<i>."""
    toks = tokens(text)
    p = TreeParser(toks, set())
    n = p.assignment()
    if p.peek()[0] != "eof":
        raise Decline(f"trailing tokens in pattern {text!r}")
    return _normalise(n, text)


def _normalise(n: Node, text: str) -> Node:
    n = n.strip()
    kids = [_normalise(k, text) for k in n.kids]
    return Node(n.kind, n.op, kids, -1, -1, n.text)


def show(n: Node) -> str:
    """Canonical text of a pattern (parenthesised where precedence requires, nothing more)."""
    return _render(n, lambda leaf: (leaf.text, PRIMARY))[0]


def _wrap(part: tuple, need: float) -> str:
    text, prec = part
    return f"({text})" if prec < need else text


def _render(n: Node, leaf) -> tuple[str, float]:
    """(text, precedence); `leaf` maps a leaf to (text, precedence) so bound subexpressions group correctly."""
    n = n.strip()
    if n.kind == "leaf":
        return leaf(n)
    if n.kind == "bin":
        a, b = n.kids
        p = BINARY[n.op]
        return f"{_wrap(_render(a, leaf), p)} {n.op} {_wrap(_render(b, leaf), p + 1)}", p
    if n.kind == "un":
        inner = _wrap(_render(n.kids[0], leaf), PREFIX)
        if inner[:1] in "-+&" and n.op in "-+&":
            inner = f"({inner})"                         # `- -x` must never become `--x`
        return n.op + inner, PREFIX
    if n.kind == "cast":
        return f"({n.text})" + _wrap(_render(n.kids[0], leaf), PREFIX), PREFIX
    if n.kind == "cond":
        c, a, b = n.kids
        return (f"{_wrap(_render(c, leaf), 1)} ? {_wrap(_render(a, leaf), 1)} : "
                f"{_wrap(_render(b, leaf), COND)}"), COND
    raise Decline(f"cannot render {n.kind}")


def size(n: Node) -> int:
    n = n.strip()
    return (0 if n.kind == "leaf" else 1) + sum(size(k) for k in n.kids)


def placeholders(n: Node) -> set[str]:
    return {x.text for x in walk(n) if x.kind == "leaf" and _PLACEHOLDER.match(x.text)}


# ---------------------------------------------------------------------------------------------- matching

def _canon(source: str, n: Node) -> str:
    return " ".join(t[1] for t in tokens(c89._mask(source[n.start:n.end])))


def match(pat: Node, node: Node, source: str, bind: dict, guard=None) -> dict | None:
    """Bind `pat` against the source tree `node`; returns the extended binding or None."""
    node = node.strip()
    if pat.kind == "leaf":
        m = _PLACEHOLDER.match(pat.text)
        if m and m.group(1) == "E":
            if not side_effect_free(node) or (guard and not guard(node)):
                return None
            key = _canon(source, node)
            if pat.text in bind:
                return bind if bind[pat.text][0] == key else None
            if any(v[0] == key for v in bind.values()):
                return None                            # distinct placeholders bind distinct text
            return {**bind, pat.text: (key, node)}
        if m:                                          # N: an integer literal
            if node.kind != "leaf" or _int(node.text) is None:
                return None
            if pat.text in bind:
                return bind if bind[pat.text][0] == node.text else None
            return {**bind, pat.text: (node.text, node)}
        want = _int(pat.text)
        if node.kind == "leaf" and want is not None and _int(node.text) == want:
            return bind
        return None
    if node.kind != pat.kind or node.op != pat.op or len(node.kids) != len(pat.kids):
        return None
    if pat.kind == "cast" and " ".join(pat.text.split()) != " ".join(node.text.split()):
        return None
    for pk, nk in zip(pat.kids, node.kids):
        bind = match(pk, nk, source, bind, guard)
        if bind is None:
            return None
    return bind


def instantiate(rhs: Node, bind: dict, source: str, need: float = PRIMARY) -> str:
    def leaf(n: Node):
        if n.text in bind:
            text, node = bind[n.text]
            return source[node.start:node.end], node.prec      # a paren node is primary already
        return n.text, PRIMARY
    text, prec = _render(rhs, leaf)
    return text if prec >= need else f"({text})"


FLOATY = re.compile(r"\b(?:f32|f64|float|double)\s+\**\s*(\w+)")
POINTERY = re.compile(r"\b[A-Za-z_]\w*\s*\*+\s*(\w+)\s*[;,)=\[]")
FLOAT_LITERAL = re.compile(r"^\d+\.\d*|^\d+[fF]$|^\.\d")


def integer_guard(source: str):
    """Reject bindings that mention a float or pointer-declared name, or a float literal: rules are integer rules."""
    masked = c89._mask(source)
    floats = set(FLOATY.findall(masked))
    pointers = set(POINTERY.findall(masked))

    def ok(node: Node) -> bool:
        for n in walk(node):
            if n.kind == "leaf":
                if n.text in floats or FLOAT_LITERAL.match(n.text):
                    return False
                if n is node.strip() and n.text in pointers:
                    return False
            if n.kind == "cast" and any(w in n.text for w in ("f32", "f64", "float", "double", "*")):
                return False
        return True
    return ok


def signature(n: Node) -> tuple:
    n = n.strip()
    return n.kind, n.op, " ".join(n.text.split()) if n.kind == "cast" else ""


def index(trees: list[Node]) -> dict:
    """Operator subtrees by root signature, outermost first, so a rule only visits nodes its root can match."""
    out: dict = {}
    for t in trees:
        for n in walk(t):
            if n.kind not in ("paren", "leaf"):
                out.setdefault(signature(n), []).append(n)
    return out


def matches(trees, lhs: Node, source: str, guard=None):
    """(node, binding) for every subtree matching `lhs`, outermost first; `trees` may be a list or an index()."""
    nodes = trees.get(signature(lhs), []) if isinstance(trees, dict) else \
        [n for t in trees for n in walk(t) if n.kind != "paren"]
    for n in nodes:
        b = match(lhs, n, source, {}, guard)
        if b is not None:
            yield n, b


def apply(source: str, trees, lhs: Node, rhs: Node, limit: int = 3, guard=None) -> list[str]:
    """Sources with one occurrence of `lhs` rewritten to `rhs` each (at most `limit`); `trees` may be an index()."""
    out, seen = [], set()
    for node, b in matches(trees, lhs, source, guard):
        new = source[:node.start] + instantiate(rhs, b, source, node.need) + source[node.end:]
        if new != source and new not in seen:
            seen.add(new)
            out.append(new)
            if len(out) >= limit:
                break
    return out

"""Strip a public function of everything a web search could find, the way an unmatched game looks.

    names, texts = anonymize(context, [original_fn, perturbed_fn, ...])

Real targets have no names: `func_80123456`, `D_80123456`, `unk_3C`. Public decomps do, and every one of them is on
the internet, so a solver with a web tool can look a named task up instead of solving it. Renaming every declared
name and scrubbing string contents makes the task look like a real unmatched function, and makes the lookup fail
(the leak detector, tools/leak_check.py, is the backstop when it does not).

Names are CLASSIFIED with pycparser over `context + first text` (functions, file-scope variables, typedefs, struct /
union / enum tags, fields, enumerators; everything else is a local, parameter or label) and REPLACED as whole words in
every text with one map, so the original and a perturbed version stay consistent. Base scalar typedefs (s8..f64) and
C keywords are kept; they say nothing about the game. Renaming cannot change instructions except through symbol
operands, which the caller must check: recompile, and compare listings with symbol operands masked
(`masked_listing`). Anything else is refused there, never assumed.
"""
from __future__ import annotations

import hashlib
import re

from pycparser import c_ast, c_parser

KEEP = {"s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "f32", "f64", "size_t"}
REGISTERS = {"zero", "at", "gp", "sp", "fp", "ra", "k0", "k1"} | {f"{p}{i}" for p, n in
                                                                  (("v", 2), ("a", 4), ("t", 10), ("s", 9))
                                                                  for i in range(n)}
STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
CHARLIT = re.compile(r"'(?:[^'\\\n]|\\.)+'")


class _Names(c_ast.NodeVisitor):
    def __init__(self):
        self.kind: dict[str, str] = {}
        self.depth = 0

    def _add(self, name, kind):
        if name and name not in KEEP and not name.startswith("__"):
            # The first classification wins; a local that shadows a global keeps the global's (one) new name.
            self.kind.setdefault(name, kind)

    def visit_FuncDef(self, node):
        self._add(node.decl.name, "func")
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    def visit_Decl(self, node):
        if isinstance(node.type, c_ast.FuncDecl):
            self._add(node.name, "func")
        elif self.depth == 0 and not getattr(self, "_in_struct", 0):
            self._add(node.name, "data")
        else:
            self._add(node.name, "field" if getattr(self, "_in_struct", 0) else "local")
        self.generic_visit(node)

    def visit_Typedef(self, node):
        self._add(node.name, "type")
        self.generic_visit(node)

    def _aggregate(self, node, kind):
        self._add(node.name, kind)
        self._in_struct = getattr(self, "_in_struct", 0) + 1
        self.generic_visit(node)
        self._in_struct -= 1

    def visit_Struct(self, node):
        self._aggregate(node, "tag")

    def visit_Union(self, node):
        self._aggregate(node, "tag")

    def visit_Enum(self, node):
        self._add(node.name, "tag")
        self.generic_visit(node)

    def visit_Enumerator(self, node):
        self._add(node.name, "enum")
        self.generic_visit(node)

    def visit_FuncDecl(self, node):
        # Parameters are locals even when the declaration sits at file scope.
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    def visit_Label(self, node):
        self._add(node.name, "local")
        self.generic_visit(node)


def _new_name(name: str, kind: str, salt: str) -> str:
    h = int(hashlib.sha256(f"{salt}:{kind}:{name}".encode()).hexdigest(), 16)
    addr = 0x80000000 | (h & 0x3FFFFC)                 # an address-shaped, word-aligned suffix, as real names have
    return {"func": f"func_{addr:08X}", "data": f"D_{addr:08X}", "type": f"UnkStruct_{addr:08X}",
            "tag": f"unk_struct_{addr:08X}", "field": f"unk_{h % 0x10000:X}", "enum": f"ENUM_{h % 0x10000:04X}",
            "local": f"var_{h % 0x100000:05x}"}[kind]


def names_of(context: str, text: str) -> dict[str, str]:
    v = _Names()
    v.visit(c_parser.CParser().parse(STRING.sub('""', context) + "\n" + STRING.sub('""', text)))
    return v.kind


def scrub_strings(text: str) -> str:
    """String contents become `?` of the same raw length: .text never depends on them, and they are searchable."""
    return STRING.sub(lambda m: '"' + "?" * (len(m.group(0)) - 2) + '"', text)


def anonymize(context: str, texts: list[str], salt: str = "anon-v1") -> tuple[dict[str, str], list[str]]:
    """(rename map, [anonymized context, *anonymized texts]). One map for all texts so they stay consistent."""
    kinds = names_of(context, texts[0])
    mapping, used = {}, set()
    for name, kind in sorted(kinds.items()):
        new = _new_name(name, kind, salt)
        while new in used or new in kinds:
            new = _new_name(new, kind, salt)           # collision: rehash; never two names onto one
        used.add(new)
        mapping[name] = new
    word = re.compile(r"\b(" + "|".join(sorted(map(re.escape, mapping), key=len, reverse=True)) + r")\b") \
        if mapping else None

    def apply(text):
        text = scrub_strings(text)
        if word is None:
            return text
        # Replace outside character literals only (a struct field named `a` must not rewrite 'a').
        parts, last = [], 0
        for m in CHARLIT.finditer(text):
            parts.append(word.sub(lambda w: mapping[w.group(1)], text[last:m.start()]))
            parts.append(m.group(0))
            last = m.end()
        parts.append(word.sub(lambda w: mapping[w.group(1)], text[last:]))
        return "".join(parts)
    return mapping, [apply(t) for t in [context, *texts]]


SYMBOL = re.compile(r"(?<![\w$.])([A-Za-z_][A-Za-z0-9_]*)(?![\w(])")


def masked_listing(rows: list[str]) -> list[str]:
    """A listing with symbol operands replaced by `SYM`: registers, numbers and mnemonics kept. Two compiles that
    differ only in names give equal masked listings."""
    out = []
    for row in rows:
        mnemonic, _, operands = row.partition(" ")
        out.append(mnemonic + " " + SYMBOL.sub(lambda m: m.group(1) if m.group(1) in REGISTERS else "SYM",
                                               operands))
    return out

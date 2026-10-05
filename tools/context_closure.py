"""The declarations a function's code depends on: a context closure over its preprocessed translation unit.

A model shown only a function cannot answer questions the compiler answered with the whole file: `x / 2` and
`x >> 1` compile identically when `x` is unsigned and differently when it is signed, and the type may live in a
typedef three headers away (audit 2026-10-03, docs/model-capability-training-audit-20261003.md). This module returns
the typedefs, struct/union/enum definitions, variables and prototypes the function references, transitively, so a
prompt can carry them.

The closure is a hypothesis about sufficiency, and callers must TEST it: compile `render(closure) + function` alone with
the same recipe and require the function's instructions to equal the full-file build's
(eval/results/edit-capability-20261002/context_tasks.py does). Nothing here compiles.

Preprocessing uses GNU cpp with the exact defines IDO's own front end receives (`cfe_args`, read from a verbose
compile), because `cc -E` misreads `-I dir` and stops on apostrophes inside `//` comments. Any difference between the
two preprocessors shows up as a failed sufficiency check, never as a silently wrong context.
"""
from __future__ import annotations

import copy
import os
import re
import subprocess
import threading
from pathlib import Path

from pycparser import c_ast, c_generator, c_parser

BUILTIN = {"void", "char", "short", "int", "long", "float", "double", "signed", "unsigned", "_Bool"}


# ------------------------------------------------------------------------------------------- preprocessing

def cfe_args(compiler: str, flags: list[str], source: str, cwd: str) -> list[str]:
    """-D/-I arguments IDO's driver passes to cfe for this file (verbose compile; the object is discarded)."""
    out = Path(os.environ.get("TMPDIR", "/tmp")) / f"cfe_args_{os.getpid()}_{threading.get_ident()}.o"
    try:
        r = subprocess.run([compiler, "-v", "-c", *flags, "-o", str(out), source], cwd=cwd,
                           capture_output=True, text=True, timeout=300)
    finally:
        out.unlink(missing_ok=True)
    line = next((l for l in (r.stderr + r.stdout).splitlines() if "/cfe " in l), "")
    return [t for t in line.split() if (t.startswith("-D") or t.startswith("-I")) and len(t) > 2
            and not t.startswith("-D__STDC__")]


GNU_UNDEF = ["-U__GNUC__", "-U__GNUC_MINOR__", "-U__GNUC_PATCHLEVEL__", "-U__STDC_VERSION__"]


def preprocess(text: str, source_path: str, cwd: str, args: list[str]) -> str | None:
    """Preprocess `text` as if it were the file at `source_path` (written beside it, so `#include "x.h"` resolves)."""
    src = Path(source_path)
    copy_path = src.parent / f".ctxpp_{os.getpid()}_{threading.get_ident()}.c"
    copy_path.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(["cpp", "-P", "-undef", "-nostdinc", "-std=gnu89", *GNU_UNDEF, *args, str(copy_path)],
                           cwd=cwd, capture_output=True, text=True, timeout=300)
    finally:
        copy_path.unlink(missing_ok=True)
    return r.stdout if r.returncode == 0 else None


def parse(pre: str) -> c_ast.FileAST:
    return c_parser.CParser().parse(pre)


# ------------------------------------------------------------------------------------------- references

class _Refs(c_ast.NodeVisitor):
    def __init__(self):
        self.names: set[str] = set()

    def visit_ID(self, node):
        self.names.add(node.name)

    def visit_IdentifierType(self, node):
        self.names.update(n for n in node.names if n not in BUILTIN)

    def visit_Struct(self, node):
        if node.name:
            self.names.add(f"struct {node.name}")
        self.generic_visit(node)

    def visit_Union(self, node):
        if node.name:
            self.names.add(f"union {node.name}")
        self.generic_visit(node)

    def visit_Enum(self, node):
        if node.name:
            self.names.add(f"enum {node.name}")
        self.generic_visit(node)

    def visit_StructRef(self, node):
        self.visit(node.name)            # `o->unk1A`: the field name is not a top-level reference


def refs(node) -> set[str]:
    v = _Refs()
    v.visit(node)
    return v.names


def _nested_definitions(node, out: set[str]) -> None:
    if isinstance(node, (c_ast.Struct, c_ast.Union)) and node.name:
        out.add(f"{'struct' if isinstance(node, c_ast.Struct) else 'union'} {node.name}")
    if isinstance(node, c_ast.Enum):
        if node.name:
            out.add(f"enum {node.name}")
        for e in (node.values.enumerators if node.values else []):
            out.add(e.name)
    for _name, child in node.children():
        _nested_definitions(child, out)


def defines(ext) -> set[str]:
    out: set[str] = set()
    if isinstance(ext, c_ast.FuncDef):
        return {ext.decl.name}
    if getattr(ext, "name", None):
        out.add(ext.name)
    _nested_definitions(ext, out)
    return out


def find_funcdef(ast: c_ast.FileAST, name: str) -> c_ast.FuncDef | None:
    return next((e for e in ast.ext if isinstance(e, c_ast.FuncDef) and e.decl.name == name), None)


def closure(ast: c_ast.FileAST, name: str) -> list[int]:
    """Indices into `ast.ext` of every top-level item the function `name` needs, transitively, in file order."""
    target = find_funcdef(ast, name)
    if target is None:
        raise KeyError(name)
    needed = refs(target) - {name}
    included: set[int] = set()
    items = [(i, e, defines(e)) for i, e in enumerate(ast.ext) if e is not target]
    changed = True
    while changed:
        changed = False
        for i, e, d in items:
            if i in included or not (d & needed):
                continue
            included.add(i)
            needed |= refs(e.decl if isinstance(e, c_ast.FuncDef) else e)
            changed = True
    return sorted(included)


# ------------------------------------------------------------------------------------------- rendering

def render(nodes, *, initializers: bool = True) -> str:
    gen = c_generator.CGenerator()
    out = []
    for n in nodes:
        if isinstance(n, c_ast.FuncDef):
            out.append(gen.visit(n.decl) + ";")
            continue
        if not initializers and isinstance(n, c_ast.Decl) and n.init is not None:
            n = copy.copy(n)
            n.init = None
        out.append(gen.visit(n) + ";")
    return "\n".join(out) + "\n"


def render_function(funcdef: c_ast.FuncDef) -> str:
    return c_generator.CGenerator().visit(funcdef).rstrip() + "\n"


def find_function_text(pre: str, name: str) -> str | None:
    """The brace-matched definition of `name` in preprocessed text (a prototype is skipped)."""
    for m in re.finditer(rf"(?m)^[^\n;{{}}]*\b{re.escape(name)}\s*\(", pre):
        depth, i = 0, m.end() - 1
        while i < len(pre):                       # the parameter list
            depth += {"(": 1, ")": -1}.get(pre[i], 0)
            i += 1
            if depth == 0:
                break
        brace = i + len(pre[i:]) - len(pre[i:].lstrip())
        if not pre.startswith("{", brace):        # a prototype or a call; ANSI definitions only
            continue
        depth, j = 0, brace
        while j < len(pre):
            depth += {"{": 1, "}": -1}.get(pre[j], 0)
            j += 1
            if depth == 0:
                return pre[m.start():j]
    return None


# ------------------------------------------------------------------------------------------- counterfactual facts

SIGN_FLIP = {"s8": "u8", "u8": "s8", "s16": "u16", "u16": "s16", "s32": "u32", "u32": "s32",
             "char": "unsigned char", "unsigned char": "signed char", "signed char": "unsigned char",
             "short": "unsigned short", "unsigned short": "short", "int": "unsigned int", "unsigned int": "int",
             "unsigned": "int", "long": "unsigned long", "unsigned long": "long"}
WIDTH_FLIP = {"s8": "s16", "u8": "u16", "s16": "s32", "u16": "u32", "s32": "s16", "u32": "u16",
              "char": "short", "short": "int", "int": "short", "unsigned char": "unsigned short",
              "unsigned short": "unsigned int", "unsigned int": "unsigned short"}


def _scalar_decls(node, path=()):
    """(path, Decl, TypeDecl's IdentifierType) for every declaration whose base type is a named integer type."""
    if isinstance(node, (c_ast.Decl, c_ast.Typedef)):
        t = node.type
        while isinstance(t, (c_ast.PtrDecl, c_ast.ArrayDecl)):
            t = t.type
        if isinstance(t, c_ast.TypeDecl) and isinstance(t.type, c_ast.IdentifierType):
            yield node, t.type
    for _name, child in node.children():
        yield from _scalar_decls(child)


def facts(nodes):
    """Candidate single-fact changes: (index of the top-level node, ordinal of the declaration inside it,
    declared name, old type, new type, kind)."""
    for i, top in enumerate(nodes):
        node = top.decl if isinstance(top, c_ast.FuncDef) else top
        for k, (decl, ident) in enumerate(_scalar_decls(node)):
            old = " ".join(ident.names)
            for kind, table in (("signedness", SIGN_FLIP), ("width", WIDTH_FLIP)):
                if old in table and decl.name:
                    yield i, k, decl.name, old, table[old], kind


def mutate(nodes, i: int, k: int, new: str):
    """A deep copy of `nodes` with the k-th scalar declaration inside node i retyped to `new`."""
    out = copy.deepcopy(nodes)
    node = out[i].decl if isinstance(out[i], c_ast.FuncDef) else out[i]
    _decl, ident = list(_scalar_decls(node))[k]
    ident.names = new.split()
    return out

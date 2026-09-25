"""Emit the four m2c contexts (PROTOCOL.md) from the target repo's ctx.c. Run with a python that has pycparser.
Writes to E/ctx-<ARM>.c; FULL is re-emitted through the same generator so the arms differ only by the ablation."""
import copy, re
from pathlib import Path
from pycparser import c_ast, c_generator, c_parser

E = Path.home() / "decomp/experiments/context-ablation-20260924"
gen = c_generator.CGenerator()


class Incomplete(c_ast.NodeVisitor):
    def __init__(self):
        self.n = 0

    def visit_Typedef(self, node):
        t = node.type.type if isinstance(node.type, c_ast.TypeDecl) else None
        if isinstance(t, (c_ast.Struct, c_ast.Union)) and t.name is None:
            t.name = f"__anon_{node.name}"
        self.generic_visit(node)

    def visit_Struct(self, node):
        if node.decls is not None:
            node.decls = None
            self.n += 1
        # no generic_visit: nested members are gone

    visit_Union = visit_Struct


def is_func(e):
    return isinstance(e, c_ast.FuncDef) or (isinstance(e, c_ast.Decl) and isinstance(e.type, c_ast.FuncDecl))


def arms(preprocessed: str) -> dict[str, str]:
    """FULL / NOLAYOUT / NOPROTO / NONE contexts from one preprocessed context text (all re-emitted alike)."""
    ast = c_parser.CParser().parse(re.sub(r"(?m)^#.*$", "", preprocessed))
    nolayout = copy.deepcopy(ast)
    Incomplete().visit(nolayout)
    noproto = copy.deepcopy(ast)
    noproto.ext = [e for e in noproto.ext if not is_func(e)]
    return {"FULL": gen.visit(ast), "NOLAYOUT": gen.visit(nolayout), "NOPROTO": gen.visit(noproto), "NONE": ""}

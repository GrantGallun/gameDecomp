"""tools/context_closure.py on hand-written preprocessed C (no compiler: sufficiency is the caller's compile test)."""
from tools import context_closure as cc

PRE = """
typedef int s32;
typedef short s16;
typedef unsigned short u16;
struct Unused { int z; };
struct Inner { s16 lo; u16 hi; };
typedef struct Obj { struct Inner in; s32 count; } Obj;
extern s32 gTotal;
extern s32 gOther;
enum Mode { MODE_A, MODE_B };
s32 helper(Obj *o);
static s32 other(void) { return gOther; }
s32 target(Obj *o) {
    s32 x = o->in.lo / 2;
    gTotal += helper(o) + MODE_B;
    return x;
}
"""


def _closure_text():
    ast = cc.parse(PRE)
    return cc.render([ast.ext[i] for i in cc.closure(ast, "target")])


def test_closure_follows_references_transitively_and_stops():
    text = _closure_text()
    for needed in ("typedef int s32;", "struct Inner", "struct Obj", "extern s32 gTotal;", "helper(Obj *o);",
                   "MODE_B", "typedef short s16;", "u16"):
        assert needed in text, needed
    for unneeded in ("Unused", "gOther", "other("):
        assert unneeded not in text, unneeded


def test_a_field_name_is_not_a_top_level_reference():
    ast = cc.parse("typedef int s32;\nextern s32 count;\nstruct S { s32 count; };\n"
                   "s32 f(struct S *p) { return p->count; }\n")
    text = cc.render([ast.ext[i] for i in cc.closure(ast, "f")])
    assert "extern" not in text and "struct S" in text


def test_find_function_text_skips_prototypes_and_calls():
    text = cc.find_function_text("int g(int a);\nint h(void) { return g(1); }\nint g(int a)\n{\n  return a;\n}\n", "g")
    assert text.startswith("int g(int a)\n{") and text.rstrip().endswith("}")


def test_facts_retype_one_scalar_declaration():
    ast = cc.parse(PRE)
    nodes = [ast.ext[i] for i in cc.closure(ast, "target")]
    fs = [f for f in cc.facts(nodes) if f[2] == "lo" and f[5] == "signedness"]
    assert fs and fs[0][3:5] == ("s16", "u16")
    i, k = fs[0][0], fs[0][1]
    before, after = cc.render(nodes), cc.render(cc.mutate(nodes, i, k, "u16"))
    changed = [(a, b) for a, b in zip(before.split("\n"), after.split("\n")) if a != b]
    assert len(changed) == 1 and "u16 lo;" in changed[0][1]
    assert cc.render(nodes) == before                    # mutate copies, never edits the caller's nodes

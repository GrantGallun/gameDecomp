"""Tests for trace-directed call repair."""

from solver import tracefix



# ------------------------------- pointer-parameter naming (replay regression)

def test_finds_pointer_param_with_struct_keyword():
    """`struct T *p` has two words before the star.

    The original pattern allowed exactly one, so it never matched a candidate
    that spelled the type with `struct`, fell back to a literal "param0", and
    rewrote calls to reference an identifier that does not exist.
    """
    code = ("struct RacePlayer { s32 a; };\n"
            "s32 f(struct RacePlayer *player)\n{\n    return 0;\n}\n")
    assert tracefix.find_ptr_param(code) == "player"


def test_finds_pointer_param_with_plain_type():
    code = "void f(RacePlayer *p)\n{\n    g();\n}\n"
    assert tracefix.find_ptr_param(code) == "p"


def test_prefers_the_definition_over_an_extern_prototype():
    """The externs above the function must not supply the name."""
    code = ("extern void other(Gfx *gfx);\n"
            "void f(RacePlayer *player)\n{\n    g();\n}\n")
    assert tracefix.find_ptr_param(code) == "player"


def test_declines_when_no_pointer_parameter_can_be_named():
    code = "void f(void)\n{\n    g();\n}\n"
    assert tracefix.find_ptr_param(code) == ""


def test_fix_calls_declines_rather_than_inventing_param0():
    """An undeterminable pointer name must leave the candidate untouched.

    Measured: a candidate compiling at 95.90 was rewritten to reference
    `param0->` when its parameter was `player`, and stopped compiling.
    """
    code = "void f(void)\n{\n    h(1);\n}\n"
    out, log = tracefix.fix_calls(code, "")
    assert out == code
    assert any("could not be named" in l for l in log)


def test_declines_member_access_the_candidate_does_not_declare():
    """`&gAssetHandles->f3e` asserts a struct the candidate may not have.

    Measured: func_8005804C compiled at 61.84 and the rewrite turned it into
    "Selector requires struct/union pointer as left hand side".
    """
    code = "void f(s32 *arg0)\n{\n    g(blockIdx);\n}\n"
    assert tracefix._to_c("&gAssetHandles->f3e", "arg0", code) is None


def test_allows_member_access_the_candidate_does_declare():
    code = ("struct H { s32 f3e; };\n"
            "extern struct H *gAssetHandles;\n"
            "void f(s32 *arg0)\n{\n    g(1);\n}\n")
    assert tracefix._to_c("&gAssetHandles->f3e", "arg0", code) == \
        "&gAssetHandles->f3e"


def test_plain_address_of_symbol_still_allowed():
    code = "void f(s32 *arg0)\n{\n    g(1);\n}\n"
    assert tracefix._to_c("&gList", "arg0", code) == "&gList"

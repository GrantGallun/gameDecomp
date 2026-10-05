"""The placeholder rewrite must FIRE on the residuals it was written for.

Written after the first version silently declined on `? *var_s3;` -- its pattern required the `?` to be
followed directly by an identifier -- and the sweep reported "the error moved rather than cleared" on
six drafts where the same line was still sitting there unrewritten. A pass that returns nothing looks
exactly like a pass with nothing to do, so both halves are asserted here.
"""
from __future__ import annotations

from solver import m2c_placeholders as mp


def test_fires_on_the_litob_prototype():
    """The residual that motivated the module: cfe stops dead at this line and judges nothing after."""
    src = "u64 __ull_rem(s32, s32, s32, s32);\n? lldiv(s32 *, s32, s32);\nextern ? xlitob_data_0000;\n"
    out, names = mp.rewrite(src)
    assert "?" not in out
    assert "s32 lldiv(s32 *, s32, s32);" in out
    assert "extern s32 xlitob_data_0000;" in out
    assert names == ["lldiv", "xlitob_data_0000"]


def test_fires_on_a_local_placeholder():
    """`? sp30;` inside a body. m2c writes these for every stack slot whose type it lost."""
    out, names = mp.rewrite("s32 f(void) {\n    ? sp30;\n    s32 sp2C;\n}\n")
    assert out == "s32 f(void) {\n    s32 sp30;\n    s32 sp2C;\n}\n"
    assert names == ["sp30"]


def test_fires_on_an_unknown_pointee():
    """The shape that got past the first pattern, and the reason this test file exists."""
    out, names = mp.rewrite("    ? *var_s3;\n")
    assert out == "    s32 *var_s3;\n"
    assert names == ["var_s3"]


def test_fires_inside_a_parameter_list():
    out, _names = mp.rewrite("void g(? *, s32);\n")
    assert out == "void g( s32 *, s32);\n"


def test_fires_on_a_NAMED_placeholder_in_a_parameter_list():
    """THE RESIDUAL THAT COST A WHOLE MEASUREMENT (2026-09-21).

    `^[ \\t]*` anchored DECL_LINE to a line start and PARAM's lookahead required punctuation after the
    token, so `? a0_unk4` inside a signature matched NEITHER rule. On the size-bucketed frame that was
    36 raw `?` tokens against 18 found and 12 left in place, and the arms measured without this fix read
    5.0% conversion / 0 exact against 20.0% / 2 with it. Taken verbatim from the frame's own drafts.
    """
    src = "u64 __ll_mul(s64 a0_unk0, ? a0_unk4, s64 a1_unk0, ? a1_unk4) {\n"
    out, names = mp.rewrite(src)
    assert "?" not in out
    assert out == "u64 __ll_mul(s64 a0_unk0, s32 a0_unk4, s64 a1_unk0, s32 a1_unk4) {\n"
    assert names == ["a0_unk4", "a1_unk4"]


def test_fires_on_named_placeholders_before_a_variadic_tail():
    """`? arg3, ...` -- the token's right-hand neighbour is a comma, and the tail must survive intact."""
    src = "void osSyncPrintf(s8 *fmt, ? arg1, ? arg2, ? arg3, ...) {\n}\n"
    out, names = mp.rewrite(src)
    assert out == "void osSyncPrintf(s8 *fmt, s32 arg1, s32 arg2, s32 arg3, ...) {\n}\n"
    assert names == ["arg1", "arg2", "arg3"]


def test_fires_on_a_named_pointer_parameter():
    out, names = mp.rewrite("void h(? *ctx, s32 n);\n")
    assert out == "void h(s32 *ctx, s32 n);\n"
    assert names == ["ctx"]


def test_the_two_rules_are_a_union_and_cover_both_parameter_shapes():
    """A nameless parameter is PARAM's, a named one is DECL_LINE's, and a draft carrying both must lose
    every `?`. Dropping either rule loses the shape the other cannot see."""
    src = "void g(? *, ? n, s32 k);\n"
    out, names = mp.rewrite(src)
    assert "?" not in out
    assert out == "void g( s32 *, s32 n, s32 k);\n"
    assert names == ["n"]


def test_a_parameter_name_that_looks_like_a_type_is_still_replaced():
    """`? s32` is m2c saying "a parameter whose type I lost, named s32". The name is not a type; the
    substitution is still the honest move and the object decides."""
    out, names = mp.rewrite("void g(? s32);\n")
    assert out == "void g(s32 s32);\n"
    assert names == ["s32"]


def test_declines_on_a_bare_question_mark_with_nothing_to_type():
    """`?` with no name and no punctuation after it is not a declaration this module can type. It must be
    left exactly as it was rather than becoming `s32` with a name that is not there."""
    src = "s32 f(void) {\n    return ?;\n}\n"
    assert mp.rewrite(src) == (src, [])


def test_a_ternary_is_left_alone():
    """Ordinary C: a `?` used as an operator has no declaration for this module to type, and the
    identifiers on either side of it must not be read as a name being declared."""
    src = "s32 f(s32 a, s32 b) {\n    return a ? b : 0;\n}\n"
    assert mp.rewrite(src) == (src, [])


def test_a_question_mark_at_the_end_of_a_declaration_is_not_invented_into_one():
    """`char *s = "?";` -- the token is inside a string literal, which `_masked` blanks, so neither rule
    may see it. This is the shape that made the module start masking strings at all."""
    src = 'char *s = "?";\ns32 f(void) {\n    return 0;\n}\n'
    assert mp.rewrite(src) == (src, [])


def test_evidence_beats_the_default():
    """A name the KB has evidence for must not be overwritten by the fallback."""
    out, _names = mp.rewrite("    ? sp30;\n", widths={"sp30": "u16"})
    assert out == "    u16 sp30;\n"


def test_declines_on_clean_source():
    """The common case. A rewrite that touched this would corrupt every draft in the tree."""
    src = 's32 f(s32 a) {\n    return a + 1;\n}\n'
    assert mp.rewrite(src) == (src, [])


def test_declines_on_a_question_mark_in_prose():
    """m2c's own header comment asks a question; that is not a type."""
    src = '// is this ? or not\ns32 f(void) { return 0; }\n'
    assert mp.rewrite(src) == (src, [])


def test_variants_vary_one_placeholder_at_a_time_and_stay_bounded():
    src = "? f(void);\n? g(void);\n? h(void);\n"
    got = mp.variants(src, limit=2)
    assert len(got) == 2 * (len(mp.FALLBACKS) - 1)
    labels = [label for label, _code in got]
    assert labels[0].startswith("placeholder0=")
    # only the targeted offset changes
    for _label, code in got:
        assert code.count("?") == 2

"""The registry must FIRE on the ten signatures that are 90% of failures.

Every stderr string here is verbatim from the attempts table.
"""

from solver import compilefix


REAL_ERRORS = {
    "cfe: Error: /home/grant/decomp/sbk1/nonmatchings/Fdrumsoff/"
    ".build-source.jQsTrV.c, line 7: Syntax Error": "Syntax Error",

    "cfe: Error: /home/grant/decomp/sbk1/nonmatchings/setRaceCameraMode/"
    ".build-source.mByVeu.c, line 6: Duplicate member 'dpad00'":
        "Duplicate member X",

    "cfe: Error: /home/grant/decomp/sbk1/nonmatchings/x/.build-source.a.c,"
    " line 12: 'gRelocatableHeapUsedBlockCount' undefined; reoccurrences"
    " will not be reported.": "X undefined; reoccurrences will not be reported.",

    "ERROR: The C file contains a do-while loop.":
        "ERROR: The C file contains a do-while loop.",

    "cfe: Error: /home/grant/decomp/sbk1/nonmatchings/y/.build-source.b.c,"
    " line 1: Unknown character ` ignored": "Unknown character ` ignored",
}


def test_signature_normalises_real_stderr():
    for raw, expected in REAL_ERRORS.items():
        assert compilefix.signature(raw) == expected, raw[:60]


def test_signature_is_stable_across_attempts():
    """Two attempts on different functions, same cause, one signature."""
    a = ("cfe: Error: /a/nonmatchings/f/.build-source.AAA.c, line 7:"
         " Syntax Error")
    b = ("cfe: Error: /b/nonmatchings/g/.build-source.ZZZ.c, line 41:"
         " Syntax Error")
    assert compilefix.signature(a) == compilefix.signature(b)


def test_empty_stderr_has_no_signature():
    assert compilefix.signature("") == ""
    assert compilefix.signature(None) == ""


# --- the 50% bucket splits by SOURCE, not by message ------------------------

FDRUMSOFF = ('#include "common.h"\n\n'
             "s32 Fdrumsoff(PlayerCommandState *arg0, s32 arg1) {\n"
             "    arg0->pdrums = NULL;\n    return arg1;\n}\n")
FCUTOFF = ('#include "common.h"\n\n'
           "s32 Fcutoff(PlayerCommandState *arg0, u8 *arg1) {\n"
           "    arg0->unkC2 = arg1->unk0;\n}\n")
NO_PARAMS = ('#include "common.h"\n\n'
             "void f(void) {\n    MenuSprite *sp = 0;\n}\n")
SYNTAX = "cfe: Error: /a/.build-source.A.c, line 7: Syntax Error"


def test_undeclared_parameter_type_routes_to_typedecl():
    sig, fixes = compilefix.dispatch(SYNTAX, FDRUMSOFF, "Fdrumsoff", {"s32"})
    assert sig == compilefix.UNDECLARED_PARAM_TYPE
    assert [f.name for f in fixes] == ["typedecl", "globals"]


def test_contradicted_primitive_pointer_routes_to_byte_index_first():
    """u8 is a known type, so typedecl must not be tried first."""
    sig, fixes = compilefix.dispatch(SYNTAX, FCUTOFF, "Fcutoff",
                                     {"s32", "u8"})
    assert sig == compilefix.CONTRADICTED_POINTER
    assert fixes[0].name == "byte-index"


def test_undeclared_type_outside_the_parameter_list_is_its_own_signature():
    sig, _fixes = compilefix.dispatch(SYNTAX, NO_PARAMS, "f", set())
    assert sig == compilefix.UNDECLARED_ELSEWHERE


def test_non_syntax_signatures_are_not_refined():
    raw = "ERROR: The C file contains a do-while loop."
    sig, fixes = compilefix.dispatch(raw, FDRUMSOFF, "Fdrumsoff", {"s32"})
    assert sig == raw
    assert [f.name for f in fixes] == ["do-while"]


def test_undefined_symbol_routes_to_globals():
    raw = ("cfe: Error: /a/.build-source.A.c, line 12: 'gFoo' undefined;"
           " reoccurrences will not be reported.")
    _sig, fixes = compilefix.dispatch(raw, FDRUMSOFF, "Fdrumsoff", {"s32"})
    assert [f.name for f in fixes] == ["globals"]


# --- unroutable errors are a finding, not a silence -------------------------

def test_unknown_signature_returns_no_fixes():
    raw = "cfe: Error: /a/.build-source.A.c, line 3: Something brand new"
    sig, fixes = compilefix.dispatch(raw, FDRUMSOFF, "Fdrumsoff", {"s32"})
    assert sig == "Something brand new"
    assert fixes == ()


def test_gaps_reports_unrouted_signatures_worst_first():
    counted = {"Something brand new": 40,
               "Another unrouted thing": 90,
               "ERROR: The C file contains a do-while loop.": 316,
               "Duplicate member X": 143}
    assert compilefix.gaps(counted) == [("Another unrouted thing", 90),
                                        ("Something brand new", 40)]


def test_known_unfixable_are_not_reported_as_gaps():
    """Recorded reasons, so they do not resurface as findings forever."""
    for sig in compilefix.KNOWN_UNFIXABLE:
        assert compilefix.gaps({sig: 100}) == []
        assert compilefix.KNOWN_UNFIXABLE[sig]


def test_known_unfixable_keys_match_what_signature_emits():
    """A registry keyed by a string is only as good as the function that
    produces the string. These keys were first copied from a census script
    truncating at 60 chars while signature() truncates at 64, so none matched
    and every one resurfaced as a gap."""
    samples = {
        "ERROR: Compiled object has no text symbols. Check for type conflicts"
        " or include issues.": None,
        "cfe: Error: /a/.build-source.A.c, line 6: Duplicate member 'dpad00'":
            None,
        "cfe: Error: /a/.build-source.A.c, line 87: Constants must have"
        " arithmetic type.": None,
    }
    for raw in samples:
        assert compilefix.signature(raw) in compilefix.KNOWN_UNFIXABLE, raw[:50]


def test_inner_line_numbers_are_normalised():
    """`at line 13` split one cause into four signatures of 33/28/25/20."""
    a = ("cfe: Error: /a/.build-source.A.c, line 5: redeclaration of 'Vec3i';"
         " previous declaration at line 13 in file 'b.h'")
    b = ("cfe: Error: /a/.build-source.B.c, line 9: redeclaration of 'Gfx';"
         " previous declaration at line 30 in file 'c.h'")
    assert compilefix.signature(a) == compilefix.signature(b)
    assert compilefix.signature(a) in compilefix.REGISTRY


def test_syntax_error_is_not_reported_as_a_gap():
    """It routes only after refinement; the first gaps() run listed it at 50%
    as the top unrouted signature while every sub-signature had an entry."""
    assert compilefix.gaps({"Syntax Error": 2013}) == []


def test_undeclared_type_not_on_a_parameter_routes_to_typedecl_first():
    """typedecl reads pointer LOCALS too. Routing this signature to globals
    alone meant the locals support could never fire: 66 of 143 functions
    stopped at 'every registered fix declined' with the fix already built."""
    code = ('#include "common.h"\n\ns32 MusAsk(s32 arg0) {\n'
            "    PlayerCommandState *var_v1;\n    return var_v1->unkC2;\n}\n")
    sig, fixes = compilefix.dispatch(SYNTAX, code, "MusAsk", {"s32"})
    assert sig == compilefix.UNDECLARED_ELSEWHERE
    assert [f.name for f in fixes] == ["typedecl", "globals"]


# --- unbalanced braces: truncated or brace-damaged generations --------------

# attempt 2863, done_reason 'length': the model looped declaring a0..a1187 and
# the budget ran out mid-declaration. Head and tail kept; the property under
# test -- one `{` never closed -- is the one the full 8,712-char source has.
TRUNCATED_STDERR = (
    "cfe: Error: /home/grant/decomp/sbk1/nonmatchings/"
    "pushRaceCourseSurfaceBoundaryWithVelocity/.build-source.gHEtCr.c,"
    " line 121: Syntax Error")
TRUNCATED = (
    '#include "common.h"\n\n'
    "/*  function  */\n"
    "void pushRaceCourseSurfaceBoundaryWithVelocity(s32 *pX, s32 *pY, s32 v)\n"
    "{\n"
    "    /*  local variables – the compiler keeps all of them on the stack\n"
    "        (no optimisation removes them, so we declare them all to force\n"
    "        the same stack layout)  */\n"
    "    s32 a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15;\n"
    "    s32 a1185, a1186, a1187,")

# attempt 22680, done_reason 'stop', verbatim: the `if` opening the first
# branch was dropped, leaving its `}` to close the function early.
FIXEDSINE_STDERR = (
    "cfe: Error: /home/grant/decomp/sbk1/nonmatchings/fixedSine/"
    ".build-source.sHyaJa.c, line 15: Syntax Error")
FIXEDSINE = (
    '#include "common.h"\n\n'
    "/* Widths are binary facts from the evidence tier; extents are\n"
    "   deliberately unspecified. Generated by solver/globaldecl.py. */\n"
    "extern s16 gSineTable[];\n\n"
    "// This is a decompilation attempt by the m2c tool.\n"
    "// Function/type definitions might be missing or incomplete.\n"
    "// The code will likely not compile without further modification.\n\n"
    "s16 fixedSine(s16 arg0) {\n"
    "    temp_t8 = (s16)(arg0 & 0xFFF);\n"
    "        return 0x1000;\n"
    "    }\n"
    "    if (temp_t8 == 0xC00) {\n"
    "        return -0x1000;\n"
    "    }\n"
    "    return (s16) ((s16) gSineTable[temp_t8] >> 3);\n"
    "}")


def test_unbalanced_braces_fires_on_a_length_truncated_generation():
    """The motivating residual. Before this signature it refined to an
    UNDECLARED_* bucket and spent typedecl/globals compiles on source that no
    zero-token repair can finish."""
    sig, fixes = compilefix.dispatch(
        TRUNCATED_STDERR, TRUNCATED,
        "pushRaceCourseSurfaceBoundaryWithVelocity", {"s32"})
    assert sig == compilefix.UNBALANCED_BRACES
    assert fixes == ()


def test_unbalanced_braces_fires_on_an_early_close():
    sig, fixes = compilefix.dispatch(FIXEDSINE_STDERR, FIXEDSINE, "fixedSine",
                                     {"s16"})
    assert sig == compilefix.UNBALANCED_BRACES
    assert fixes == ()


def test_unbalanced_braces_is_a_recorded_reason_not_a_gap():
    assert compilefix.UNBALANCED_BRACES in compilefix.KNOWN_UNFIXABLE
    assert compilefix.gaps({compilefix.UNBALANCED_BRACES: 226}) == []


def test_braces_inside_comments_and_literals_are_not_structure():
    """0 of 23,788 compiling attempts fire; counting these would break that."""
    code = ('#include "common.h"\n\n'
            "s32 f(s32 arg0) {\n"
            "    /* } unmatched in a block comment */\n"
            "    // { unmatched in a line comment\n"
            "    char open = '{';\n"
            "    char close = '\'';\n"
            '    const char *s = "}} \\" {";\n'
            "    return arg0;\n"
            "}\n")
    assert not compilefix.brace_imbalance(code)


def test_unterminated_block_comment_counts_as_truncation():
    assert compilefix.brace_imbalance("s32 f(void) { return 0; } /* cut")


def test_balanced_sources_keep_their_existing_routing():
    """The brace check runs first, so it must not swallow the sub-signatures
    that were already measured to route correctly."""
    for code in (FDRUMSOFF, FCUTOFF, NO_PARAMS):
        assert not compilefix.brace_imbalance(code)


# --- path normalisation ------------------------------------------------------

def test_signature_strips_any_source_path_not_only_build_source():
    """`candidate.c, line 7:` survived normalisation, splitting 1,331 failures
    across 122 line-numbered signatures that nothing routed."""
    assert compilefix.signature(
        "cfe: Error: candidate.c, line 7: Syntax Error") == "Syntax Error"
    a = "cfe: Error: candidate.c, line 14: Syntax Error"
    b = "cfe: Error: candidate.c, line 15: Syntax Error"
    assert compilefix.signature(a) == compilefix.signature(b)


def test_candidate_c_undefined_symbol_now_routes_to_globals():
    raw = ("cfe: Error: candidate.c, line 12: 'gFoo' undefined;"
           " reoccurrences will not be reported.")
    _sig, fixes = compilefix.dispatch(raw, FDRUMSOFF, "Fdrumsoff", {"s32"})
    assert [f.name for f in fixes] == ["globals"]


def test_messages_without_a_path_are_left_alone():
    raw = "ERROR: The C file contains a do-while loop."
    assert compilefix.signature(raw) == raw

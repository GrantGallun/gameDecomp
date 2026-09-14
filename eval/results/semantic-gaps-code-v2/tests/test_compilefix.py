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

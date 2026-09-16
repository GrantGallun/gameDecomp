"""Tests for chained compile recovery and the undeclared-identifier fixer.

Shapes are the real census cases (2026-09-15): an m2c placeholder that hides undeclared stack slots
(updateRacePlayerSurfaceContact), declarable globals (drawRaceSetupPlayerCountPrompt class), a
missing struct member IDO also calls "undefined" (__osContGetInitData), aggregates and flattened
members that must decline, and the line-shift trap that once made re-declaring a name look like
progress. The chain FIRES on its motivating residual and stops when nothing advances.
"""
from dataclasses import dataclass

from solver import compile_chain as C
from solver import undeclared_identifiers as U


@dataclass
class Attempt:
    compiled: bool
    compiler_stderr: str
    score: float = 0.0
    frontend: dict | None = None


def undefined(line, name):
    return f"cfe: Error: candidate.c, line {line}: '{name}' undefined; reoccurrences will not be reported.\n"


def test_names_come_from_ido_and_clang_diagnostics():
    text = undefined(9, "sp130") + "error: use of undeclared identifier 'gFoo'\n" + undefined(12, "sp130")
    assert U.undefined_names(text) == ["sp130", "gFoo"]


def test_declarations_follow_each_names_use():
    source = ("s32 f(s32 *arg0) {\n    s32 x;\n"
              "    x = gCount + D_80112233[2];\n    g(&sp88, &gByteBuf);\n    sp130 = x;\n    return sp138;\n}\n")
    diagnostics = "".join(undefined(3, n) for n in ("gCount", "D_80112233", "sp88", "gByteBuf", "sp130", "sp138"))
    rows, report = U.propose(source, "f", diagnostics)
    assert len(rows) == 1
    declared = {d["name"]: d["declaration"] for d in report["declared"]}
    assert declared == {"gCount": "extern s32 gCount;", "D_80112233": "extern s32 D_80112233[];",
                        "sp88": "u8 sp88[168];", "gByteBuf": "extern u8 gByteBuf;",
                        "sp130": "s32 sp130;", "sp138": "s32 sp138;"}
    text = rows[0][1]
    assert text.index("extern s32 gCount;") < text.index("s32 f(")          # externs above the function
    assert text.index("u8 sp88[168];") > text.index("{")                   # locals first in the body


def test_declines_members_parameters_aggregates_and_names_used_elsewhere():
    source = ("void __osContGetInitData(u8 *pattern, OSContStatus *data) {\n    __OSContRequesFormat spC;\n"
              "    OSContStatus *var_a1;\n    var_a1 = data;\n    var_a1->errno = spC.data[0xA];\n"
              "    gHeap.size = unk18;\n}\n")
    diagnostics = "".join(undefined(5, n) for n in ("data", "gHeap", "unk18", "spriteIndex"))
    rows, report = U.propose(source, "__osContGetInitData", diagnostics)
    assert rows == []
    reasons = {d["name"]: d["reason"] for d in report["declines"]}
    assert reasons["data"].startswith("used as a member name")
    assert reasons["gHeap"].startswith("used as an aggregate")
    assert reasons["unk18"].startswith("bare unkNN")
    assert reasons["spriteIndex"] == "not used in the function body"


SURFACE = ("#include \"common.h\"\nextern M2C_UNK gRacePlayerGroundProbeOffsets;\n\n"
           "s32 updateRacePlayerSurfaceContact(struct RacePlayer *player) {\n    s16 sp1D8;\n\n"
           "    g(&gRacePlayerGroundProbeOffsets, player);\n    sp130 = sp1D8;\n    return sp130;\n}\n")


def test_chain_fires_through_placeholder_then_undeclared_slots_to_a_compile():
    def score(label, code):
        if "M2C_UNK" in code:
            return Attempt(False, "cfe: Error: candidate.c, line 2: Syntax Error\n")
        if "s32 sp130;" not in code:
            return Attempt(False, undefined(8, "sp130"))
        return Attempt(True, "", score=41.0)

    root = score("root", SURFACE)
    scored, log = C.chain("updateRacePlayerSurfaceContact", "seed", SURFACE, root, score)
    assert [label for label, _c, _a in scored] == ["seed+placeholders:typed", "seed+placeholders:typed+undeclared:declare"]
    assert scored[-1][2].compiled and "extern u8 gRacePlayerGroundProbeOffsets;" in scored[-1][1]
    assert len(log) == 2


def test_chain_stops_when_a_declaration_resolves_nothing_despite_shifted_lines():
    source = "void f(void) {\n    x = gThing;\n}\n"
    calls = []

    def score(label, code):
        calls.append(label)
        # the declaration is added, but IDO keeps reporting the same error one line lower
        return Attempt(False, undefined(2 + code.count("extern"), "gThing"))

    root = score("root", source)
    scored, log = C.chain("f", "seed", source, root, score, rounds=6)
    assert len(scored) == 1 and log[-1]["stopped"] == "no progress"


def test_chain_does_nothing_for_compiling_or_unfixable_sources():
    assert C.chain("f", "seed", "void f(void) {}\n", Attempt(True, ""), lambda l, c: None) == ([], [])
    unfixable = Attempt(False, "cfe: Error: candidate.c, line 3: Unacceptable operand of '-'.\n")
    scored, log = C.chain("f", "seed", "void f(void) {\n    x;\n}\n", unfixable, lambda l, c: None)
    assert scored == [] and log[0]["stopped"] == "no fix applies"


def test_a_helper_refused_do_while_is_lowered_and_counts_as_reaching_ido():
    source = ("s32 f(s32 *arg0) {\n    s32 i;\n\n    i = 0;\n    do {\n        i += 1;\n    } while (i < 4);\n"
              "    return sp130 + i;\n}\n")
    policy = "ERROR: The C file contains a do-while loop.\nWrite C code that compiles to matching assembly instead.\n"

    def score(label, code):
        if "do {" in code:
            return Attempt(False, policy)
        if "s32 sp130;" not in code:
            return Attempt(False, undefined(8, "sp130"))
        return Attempt(True, "", score=12.0)

    root = score("root", source)
    assert C.helper_blocked(root) and C.progress(root)[2] is False
    scored, log = C.chain("f", "seed", source, root, score)
    labels = [label for label, _c, _a in scored]
    assert labels[0] == "seed+do_while:for_break" and labels[-1].endswith("undeclared:declare")
    assert scored[-1][2].compiled and "do {" not in scored[-1][1]


# ---- last-resort compiling baseline (solver.compile_stub) and the C89 for-declaration hoist


def test_stub_forms_keep_the_original_text_and_structure():
    from solver import compile_stub as S
    source = ("s32 f(u8 *p) {\n    s32 x;\n    x = p->unk1;\n    if (p->unk2 > 1) {\n        x = 2;\n    }\n"
              "    for (x = p->unk3; x < 4; x++) {\n    }\n    do {\n        x--;\n    } while (p->unk4);\n"
              "    return p->unk5;\n}\n")
    expected = {3: "    /* compile-stub: x = p->unk1; */",
                4: "    if (0 /* compile-stub: p->unk2 > 1 */) {",
                7: "    for (; 0 /* compile-stub: x = p->unk3; x < 4; x++ */; ) {",
                11: "    } while (0 /* compile-stub: p->unk4 */);",
                12: "    return 0 /* compile-stub: p->unk5 */;"}
    for line, text in expected.items():
        rows, report = S.propose(source, "f", line)
        assert rows and rows[0][1].split("\n")[line - 1] == text, (line, report)
    for line in (1, 5 + 1, 13):                    # signature, closing brace, closing brace
        rows, report = S.propose(source, "f", line)
        assert rows == [] and report["declined"]


def test_stub_never_comments_out_a_closing_marker():
    from solver import compile_stub as S
    source = "void f(void) {\n    x = a /* note */ + b;\n}\n"
    rows, _ = S.propose(source, "f", 2)
    assert "/* compile-stub: x = a /* note * / + b; */" in rows[0][1]


def test_c99_for_declarations_hoist_and_decline_on_reuse():
    source = "void f(void) {\n    s32 x;\n\n    for (int i = 0; i < 4; i++) {\n        x = i;\n    }\n}\n"
    hoisted = C.hoist_for_declarations(source, "f")
    assert "    int i;\n" in hoisted and "for (i = 0; i < 4; i++)" in hoisted
    twice = "void f(void) {\n    for (int i = 0; i < 2; i++) {}\n    for (int i = 0; i < 3; i++) {}\n}\n"
    assert C.hoist_for_declarations(twice, "f") == twice


def test_stub_mode_falls_back_when_a_real_fix_leaves_the_first_error_and_reaches_a_compile():
    # _Printf shape: undeclared slots AND an unfixable member access on a u8 at the first error line.
    source = "s32 f(u8 *p) {\n    s32 x;\n    x = p->unk1;\n    spAC = x;\n    return spAC;\n}\n"

    def score(label, code):
        lines = code.split("\n")
        errors = []
        for number, text in enumerate(lines, 1):
            if "p->unk1" in text and "compile-stub" not in text:
                errors.append(f"cfe: Error: candidate.c, line {number}: Selector requires struct/union pointer as left hand side\n")
        if "s32 spAC;" not in code:
            errors.append(undefined(4 + (code.count("\n") - source.count("\n")), "spAC"))
        return Attempt(not errors, "".join(errors), score=37.5 if not errors else 0.0)

    root = score("root", source)
    assert C.chain("f", "seed", source, root, score)[0][-1][2].compiled is False      # without stubs: stuck
    scored, log = C.chain("f", "seed", source, root, score, stub=True)
    assert scored[-1][2].compiled and "/* compile-stub: x = p->unk1; */" in scored[-1][1]
    assert "s32 spAC;" in scored[-1][1]


# ------------------------------------------- C89 / target linkage is the FIRST rung
#
# Motivating residual: the admission bucket's largest clean classes. Real model output (KB receipts
# 31124 / 31125, 2026-09-16) that was correct C apart from linkage:
#
#     static inline void *f(void)   -> cfe: Syntax Error at the opening brace   (inline is C99)
#     static void *f(void)          -> "Compiled object has no text symbols"    (IDO drops it)
#
# Before this rung the ladder started at do-while and for-declarations, neither of which applies, so
# these candidates never advanced and were counted as "the model cannot write C that builds".

def test_c89_rung_fires_on_the_motivating_residual():
    source = ("#include ""common.h""\n"
              "static inline void *acquireRelocatableHeapBlockMetadata(void)\n"
              "{\n"
              "    return 0;\n"
              "}\n")
    attempt = Attempt(False, "cfe: Error: candidate.c, line 4: Syntax Error\n")
    rows, report = C.next_fixes(source, "acquireRelocatableHeapBlockMetadata", attempt)
    assert rows, "the C89 rung declined on the residual it was written for"
    label, fixed = rows[0]
    assert label == "c89:normalize_target"
    assert "inline" not in fixed and "static" not in fixed
    assert report["c89"]["applied"] is True


def test_c89_rung_declines_on_a_candidate_it_cannot_improve():
    # A source with no C99 construct and no target `static` must not burn a compile: the rung
    # declines and the ladder falls through to the IDO-error-driven fixes.
    source = "s32 f(u8 *p) {\n    return p->unk1;\n}\n"
    attempt = Attempt(False,
                      "cfe: Error: candidate.c, line 2: Selector requires struct/union pointer "
                      "as left hand side\n")
    rows, report = C.next_fixes(source, "f", attempt)
    assert all(label != "c89:normalize_target" for label, _ in rows)
    assert "c89" not in report


def test_c89_rung_precedes_the_for_declaration_rung():
    # `for (s32 i = 0; ...)` is BOTH a C89 violation and a target-linkage no-op. The C89 rung owns
    # declaration hoisting too, so it must answer first rather than the ladder reporting two fixes
    # for one problem.
    source = "static s32 f(void) {\n    for (s32 i = 0; i < 4; i++) {}\n    return 0;\n}\n"
    attempt = Attempt(False, "cfe: Error: candidate.c, line 2: Syntax Error\n")
    rows, _ = C.next_fixes(source, "f", attempt)
    assert rows[0][0] == "c89:normalize_target"
    assert "static" not in rows[0][1]

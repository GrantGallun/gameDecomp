"""Explicit candidate externs yield to the destination TU while keeping the candidate's type (eval.prepare_integration)."""
from eval import prepare_integration as pi

DEST = """#include "common.h"

u8 gFramebufferSwapHold;
const char gFmt[16] = "%d";
void drawMenuAsciiTextDefaultScale(s32 x, s32 y, const char *text);

void finishRaceStartTransition(void) {
    INCLUDE_REFERENCE_BODY();
}
"""


def test_conflicting_scalar_extern_yields_and_keeps_the_candidate_type():
    # finishRaceStartTransition and four others (2026-09-30): `extern s8 gFramebufferSwapHold;` against the TU's u8.
    cand = """extern s8 gFramebufferSwapHold;

void finishRaceStartTransition(void) {
    if (gFramebufferSwapHold != 0) {
        gFramebufferSwapHold = 0;
    }
}
"""
    out = pi.replace_function(DEST, cand, "finishRaceStartTransition")
    assert "extern s8 gFramebufferSwapHold;" not in out
    assert "if ((*(s8 *)&gFramebufferSwapHold) != 0)" in out and "(*(s8 *)&gFramebufferSwapHold) = 0;" in out
    assert "u8 gFramebufferSwapHold;" in out                      # the destination is untouched


def test_array_and_function_externs_yield_with_typed_uses():
    cand = """extern char gFmt[];
extern void drawMenuAsciiTextDefaultScale(s32, s32, void *);

void finishRaceStartTransition(void) {
    drawMenuAsciiTextDefaultScale(1, 2, gFmt);
}
"""
    out = pi.replace_function(DEST, cand, "finishRaceStartTransition")
    assert "extern char gFmt[];" not in out and "extern void drawMenuAsciiTextDefaultScale" not in out
    # the array is cast (const char[16] vs char[]); the call is never cast (a cast call can become jalr)
    assert "drawMenuAsciiTextDefaultScale(1, 2, ((char *)gFmt));" in out


def test_extern_the_destination_does_not_name_is_kept():
    cand = """extern s32 gSomethingElse;

void finishRaceStartTransition(void) {
    gSomethingElse = 1;
}
"""
    out = pi.replace_function(DEST, cand, "finishRaceStartTransition")
    assert "extern s32 gSomethingElse;" in out and "gSomethingElse = 1;" in out


def test_shadowing_local_declines_and_keeps_the_extern():
    cand = """extern s8 gFramebufferSwapHold;

void finishRaceStartTransition(void) {
    s32 gFramebufferSwapHold;
    gFramebufferSwapHold = 1;
}
"""
    assert pi._typed_uses(cand[cand.index("void finish"):], "extern s8 gFramebufferSwapHold;") is None


def test_matching_or_header_only_declarations_keep_the_extern_as_written():
    # 2026-09-30 dry run: rewriting every overlap changed two already-integrated functions' code (checksum mismatch).
    dest = DEST + "s8 gSameType;\n"
    cand = """extern s8 gSameType;
extern s32 gOnlyInAHeader;

void finishRaceStartTransition(void) {
    gSameType = gOnlyInAHeader;
}
"""
    out = pi.replace_function(dest + "void useHeader(void) { gOnlyInAHeader = 0; }\n", cand, "finishRaceStartTransition")
    assert "extern s8 gSameType;" in out and "extern s32 gOnlyInAHeader;" in out
    assert "gSameType = gOnlyInAHeader;" in out


def test_param_types_ignore_names():
    assert pi._param_types("(s32 x, const char *text)") == ["s32", "const char *"]
    assert pi._param_types("(s32, const char *)") == ["s32", "const char *"]
    assert pi._param_types("(void)") == []

"""Widening an extern WE hypothesised, when the checker states the type the use requires.

THE RESIDUAL. `wide-intake-whitelist.json`: `incompatible-int-pointer` is 65 states and the ONLY
remaining class in 8, 6 of them a single error. It became the top sole blocker BECAUSE the declaration
passes started converting states -- every one they fixed hit this as its next wall, which is the masking
mechanism the fault histogram predicted rather than a new defect.

THE CAUSE. `globals_variant` and `undeclared_identifiers` type an undeclared datum from access WIDTH,
and a width is not a type. Every fixture below is a real declaration those passes emitted, with the real
diagnostic beside it:

    initRaceItemTextureEffects         extern s32 gRaceItemEffectSpriteIds;
                                       var_s0 = gRaceItemEffectSpriteIds;
                                       assigning to 'u16 *' from 's32'          -> extern u16 X[];

    drawMainMenuModeDescriptionPanel   extern s32 mainMenuModeDescriptionTitles[];
                                       temp_a2 = mainMenuModeDescriptionTitles[i];
                                       assigning to 'u16 *' from 's32'          -> extern u16 *X[];

    acquireSoundEffectHandleNode       extern s32 gFreeSoundHandleStack[];
                                       return gFreeSoundHandleStack[i];
                                       returning from result type 'SoundHandleNode *'  -> ABSTAIN

THE CONTAMINATION BOUNDARY IS THE POINTEE. `u16 *` is a fact about width. `SoundHandleNode *` and
`MenuGlyphScript *` name structs that only a reconstructed `include/game/**` header defines, so adopting
one would silently make the repair header-assisted. Those abstain by name.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import pointer_decl_widen as pdw                                  # noqa: E402


def _conversion(line: int, pointee: str, wording: str = "assigning to", column: int = 12) -> str:
    return (f"candidate.c:{line}:{column}: error: incompatible integer to pointer conversion "
            f"{wording} '{pointee} *' (aka 'unsigned short *') from 's32' (aka 'long')\n")


BARE = ("extern s32 gRaceItemEffectSpriteIds;\n"
        "void f(void) {\n"
        "    var_s0 = gRaceItemEffectSpriteIds;\n"
        "}\n")

INDEXED = ("extern s32 mainMenuModeDescriptionTitles[];\n"
           "void f(void) {\n"
           "    temp_a2 = mainMenuModeDescriptionTitles[gMainMenuModeSelection];\n"
           "}\n")


def test_a_bare_use_becomes_an_array_that_decays():
    """THE MOTIVATING CASE: `initRaceItemTextureEffects`, its single remaining error."""
    out, changes = pdw.rewrite(BARE, _conversion(3, "u16"))
    assert "extern u16 gRaceItemEffectSpriteIds[];" in out, out
    assert "extern s32 gRaceItemEffectSpriteIds;" not in out
    applied = [c for c in changes if "after" in c]
    assert len(applied) == 1
    assert applied[0]["indexed"] is False
    assert applied[0]["pointee"] == "u16"
    # The body is untouched: only the DECLARATION moves.
    assert "var_s0 = gRaceItemEffectSpriteIds;" in out


def test_an_ASSIGNED_name_becomes_a_pointer_object_never_an_array():
    """THE REGRESSION THIS PASS CAUSED, pinned by its real shape.

    With struct pointees admitted (LOOP-5, `widen3`), the bare rule declared a display-list cursor as
    `extern Gfx X[];` -- and the draft ASSIGNS it, which C forbids for an array. `unclassified` went from
    123 errors to 729, 604 of them `array type 'Gfx[]' is not assignable`, and per-state totals still
    FELL (many `int -> pointer` errors traded for fewer array errors), so the quality ratchet read 46
    states as "better" while each carried a different wrong declaration.
    """
    for write in ("gDisplayListHead = gDisplayListHead + 8;", "gDisplayListHead++;",
                  "gDisplayListHead += 8;", "++gDisplayListHead;"):
        source = ("extern s32 gDisplayListHead;\n"
                  "void f(void) {\n"
                  "    p = gDisplayListHead;\n"
                  f"    {write}\n"
                  "}\n")
        column = source.splitlines()[2].index("gDisplayListHead") + 1
        # The pointee is a struct, so the candidate needs a game header for it to be adopted at all.
        source_with_header = '#include "game/gfx.h"\n' + source
        out, changes = pdw.rewrite(source_with_header,
                                   _conversion(4, "Gfx", column=column).replace(
                                       "(aka 'unsigned short *') ", ""))
        assert "extern Gfx *gDisplayListHead;" in out, (write, out)
        assert "extern Gfx gDisplayListHead[];" not in out, write
        applied = [c for c in changes if "after" in c]
        assert applied[0]["assigned"] is True, write


def test_a_name_both_indexed_and_assigned_abstains():
    """Array-of-pointers and pointer-to-pointer both fit that, so choosing would be a guess."""
    source = ("extern s32 gA;\n"
              "void f(void) {\n"
              "    p = gA[i];\n"
              "    gA = q;\n"
              "}\n")
    column = source.splitlines()[2].index("gA") + 1
    out, changes = pdw.rewrite(source, _conversion(3, "u16", column=column))
    assert out == source
    assert any("indexed and assigned" in c.get("declined", "") for c in changes), changes


def test_an_indexed_use_becomes_an_array_OF_POINTERS():
    """`X[i]` assigned to `u16 *` means the ELEMENT is the pointer, not the array."""
    out, changes = pdw.rewrite(INDEXED, _conversion(3, "u16"))
    assert "extern u16 *mainMenuModeDescriptionTitles[];" in out, out
    applied = [c for c in changes if "after" in c]
    assert applied[0]["indexed"] is True


def test_a_struct_pointee_abstains_in_a_candidate_with_no_game_header():
    """THE CASE THE BOUNDARY STILL PROTECTS. `acquireSoundEffectHandleNode` includes 0 game/** headers,
    so it is genuinely binary-only; adopting `SoundHandleNode *` there would be NEW assistance and would
    silently change the tier of any match that followed."""
    source = ("extern s32 gFreeSoundHandleStack[];\n"
              "void *f(void) {\n"
              "    return gFreeSoundHandleStack[temp_t6];\n"
              "}\n")
    out, changes = pdw.rewrite(source, _conversion(3, "SoundHandleNode", "returning"))
    assert out == source, "not one byte"
    declined = [c for c in changes if "declined" in c]
    assert "not a primitive scalar" in declined[0]["declined"]
    assert "NEW assistance" in declined[0]["declined"]


def test_a_struct_pointee_is_adopted_when_the_candidate_already_includes_game_headers():
    """THE CORRECTION. `drawRaceSetupPlayerCountPrompt` carries 5 of 6 includes from game/**, so the
    assistance was already taken by `header_variant` and refusing `MenuGlyphScript *` protected nothing.
    clang could only print that type because it is already declared in scope, so no layout is invented.
    """
    source = ('#include "game/menu/renderer/menu_renderer.h"\n'
              "extern s32 gRaceSetupPlayerCountPromptText;\n"
              "void f(void) {\n"
              "    drawMenuGlyphScript(0, 0, gRaceSetupPlayerCountPromptText, 0);\n"
              "}\n")
    column = source.splitlines()[3].index("gRaceSetupPlayerCountPromptText") + 1
    diagnostics = (f"candidate.c:4:{column}: error: incompatible integer to pointer conversion passing "
                   "'s32' (aka 'long') to parameter of type 'MenuGlyphScript *'\n")
    out, changes = pdw.rewrite(source, diagnostics)
    applied = [c for c in changes if "after" in c]
    assert "extern MenuGlyphScript gRaceSetupPlayerCountPromptText[];" in out, out
    # AND THE TIER TRAVELS WITH IT, rather than being reconstructed later from a strategy string.
    assert applied[0]["pointee_is_primitive"] is False
    assert applied[0]["game_headers"] == ["game/menu/renderer/menu_renderer.h"]


def test_the_receipt_labels_a_struct_pointee_header_assisted(monkeypatch):
    from eval import intake_runners
    from solver import frontend_diagnostics as fd

    source = ('#include "game/menu/renderer/menu_renderer.h"\n'
              "extern s32 gX;\n"
              "void f(void) {\n"
              "    g(gX);\n"
              "}\n")
    column = source.splitlines()[3].index("gX") + 1
    monkeypatch.setattr(fd, "analyse", lambda *a, **k: {
        "status": "rejected", "passed": False, "error_count": 1, "gates": {}, "errors": [],
        "source_sha256": "0" * 64, "diagnostics_truncated": False, "errors_truncated": False,
        "diagnostics": (f"candidate.c:4:{column}: error: incompatible integer to pointer conversion "
                        "passing 's32' to parameter of type 'MenuGlyphScript *'\n")})
    result = intake_runners.widen_pointer_declarations(
        {"candidate": source, "repo": ".", "target": "build/src/f.o"}, {})
    assert result["changed"] is True
    assert result["detail"]["authority"].startswith("HEADER-ASSISTED"), result["detail"]["authority"]


def test_a_name_this_route_never_declared_is_left_alone():
    """It may only rewrite OUR OWN hypothesis. A name with no extern here is not ours to retype."""
    source = "void f(void) {\n    var_s0 = somethingElse;\n}\n"
    out, changes = pdw.rewrite(source, _conversion(2, "u16"))
    assert out == source
    assert changes == [] or all("after" not in c for c in changes)


WORDINGS = (
    "assigning to 'u16 *' (aka 'unsigned short *') from 's32' (aka 'long')",
    "initializing 'u16 *' (aka 'unsigned short *') with an expression of type 's32'",
    "passing 's32' (aka 'long') to parameter of type 'u16 *' (aka 'unsigned short *')",
    "returning 's32' (aka 'long') from a function with result type 'u16 *' (aka 'unsigned short *')",
)


def test_every_wording_clang_uses_is_understood():
    """FOUR WORDINGS, and the pointee sits in a different place in each.

    The first version anchored the pointee after `assigning to|passing|returning` with a `[^']*` gap,
    and that gap cannot cross a quote -- so on `passing` and `returning`, where `'s32'` comes first, it
    never reached the pointee and understood one wording of four. Measured against clang 20.1.2.
    """
    subject = "mainMenuModeDescriptionTitles"
    column = INDEXED.splitlines()[2].index(subject) + 1
    for wording in WORDINGS:
        diagnostics = (f"candidate.c:3:{column}: error: incompatible integer to pointer conversion "
                       f"{wording}\n")
        out, changes = pdw.rewrite(INDEXED, diagnostics)
        applied = [c for c in changes if "after" in c]
        assert applied, f"{wording} was not understood: {changes}"
        assert applied[0]["pointee"] == "u16", wording
        assert f"extern u16 *{subject}[];" in out, wording


def test_the_column_picks_the_subject_over_the_index():
    """THE ABSTENTION THAT COST A STATE. `drawMainMenuModeDescriptionPanel` declined because BOTH
    `mainMenuModeDescriptionTitles` and `gMainMenuModeSelection` are declared on its line, and the first
    version refused to choose between them. Measured: the column points at the start of the CONVERTED
    EXPRESSION, so on `    a = titles[gSel];` it is `titles` and never the index.
    """
    source = ("extern s32 titles[];\n"
              "extern s32 gSel;\n"
              "void f(void) {\n"
              "    a = titles[gSel];\n"
              "}\n")
    column = source.splitlines()[3].index("titles") + 1
    out, changes = pdw.rewrite(source, _conversion(4, "u16", column=column))
    applied = [c for c in changes if "after" in c]
    assert applied and applied[0]["name"] == "titles", changes
    assert "extern u16 *titles[];" in out
    assert "extern s32 gSel;" in out, "the index declaration is untouched"


def test_a_column_on_the_index_widens_the_index_and_nothing_else():
    """The column is trusted, so a diagnostic that really is about the index affects only the index.
    This pins that the subject comes from the column and not from a heuristic about position."""
    source = ("extern s32 titles[];\n"
              "extern s32 gSel;\n"
              "void f(void) {\n"
              "    a = titles[gSel];\n"
              "}\n")
    column = source.splitlines()[3].index("gSel") + 1
    out, _ = pdw.rewrite(source, _conversion(4, "u16", column=column))
    assert "extern u16 gSel[];" in out
    assert "extern s32 titles[];" in out


def test_an_unrecognised_column_falls_back_and_still_refuses_an_ambiguous_line():
    """When the column lands on nothing we declared, the line-based rule applies -- and it still will
    not choose between two declarations."""
    source = ("extern s32 gA;\n"
              "extern s32 gB;\n"
              "void f(void) {\n"
              "    gA = gB;\n"
              "}\n")
    out, changes = pdw.rewrite(source, _conversion(4, "u16", column=1))
    assert out == source
    declined = [c for c in changes if "declined" in c]
    assert "ambiguous" in declined[0]["declined"]
    assert declined[0]["candidates"] == ["gA", "gB"]


def test_conflicting_pointees_for_one_name_abstain():
    """Two conversions disagreeing about the same datum is not a repair, it is a contradiction."""
    source = ("extern s32 gA;\n"
              "void f(void) {\n"
              "    p = gA;\n"
              "    q = gA;\n"
              "}\n")
    out, changes = pdw.rewrite(source, _conversion(3, "u16") + _conversion(4, "u8"))
    declined = [c for c in changes if "declined" in c]
    assert any("disagree" in d["declined"] for d in declined), changes
    assert "extern u8" not in out


def test_a_declaration_that_already_says_it_is_not_rewritten():
    source = ("extern u16 gA[];\n"
              "void f(void) {\n"
              "    p = gA;\n"
              "}\n")
    out, changes = pdw.rewrite(source, _conversion(3, "u16"))
    assert out == source
    assert any("already says that" in c.get("declined", "") for c in changes)


def test_no_conversion_diagnostic_is_a_clean_no_op():
    assert pdw.rewrite(BARE, "") == (BARE, [])
    assert pdw.rewrite(BARE, "candidate.c:1:1: error: unknown type name 'Actor'\n") == (BARE, [])


def test_the_primitive_set_is_widths_and_not_structs():
    assert {"u8", "u16", "s32", "f32", "unsigned short"} <= pdw.PRIMITIVE_POINTEES
    for rejected in ("SoundHandleNode", "MenuGlyphScript", "RaceCamera", "Actor"):
        assert rejected not in pdw.PRIMITIVE_POINTEES


def test_the_action_is_registered_and_names_its_missing_inputs():
    from eval import intake_runners
    from eval.tool_runners import NOT_APPLICABLE

    registry: dict = {}
    assert "eval.intake_runners.widen_pointer_declarations" in intake_runners.register(registry)
    result = intake_runners.widen_pointer_declarations({"candidate": BARE}, {})
    assert result["status"] == NOT_APPLICABLE
    assert "repo" in result["reason"]
    no_target = intake_runners.widen_pointer_declarations({"candidate": BARE, "repo": "."}, {})
    assert no_target["status"] == NOT_APPLICABLE
    assert "target" in no_target["reason"]


def test_it_runs_after_the_declaration_passes_in_the_sequence():
    """It edits what they emit, so ordering is part of the contract, not a preference."""
    from eval.intake_probe import SEQUENCE

    order = [a.split(".")[-1] for a in SEQUENCE]
    for earlier in ("globals_variant", "undeclared_identifiers", "header_prototypes"):
        assert order.index(earlier) < order.index("widen_pointer_declarations"), earlier

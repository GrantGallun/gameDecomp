"""Relocation-name generators fire on the 2026-09-14 motivating residuals."""
from solver import relocation_names as rn

ENTRY_FEE = """void drawRaceSplitscreenSelectEntryFee(void *arg0) {
    s8 sp40[24];
    sprintf(sp40, "%6dG", gRacePlayers->money);
}
"""
ENTRY_FEE_DIFF = """@@ -10,3 +10,3 @@
-lui    a1,%hi(gRaceSplitscreenSelectEntryFeeFormat)
-addiu    a1,a1,%lo(gRaceSplitscreenSelectEntryFeeFormat)
+lui    a1,%hi(.rodata)
+addiu    a1,a1,%lo(.rodata)
 jal    sprintf
"""

HITS = """void func_8005AE1C(void) {
    drawMenuAsciiTextDefaultScale(0x20, -0x48, "Hit", 6);
    drawMenuAsciiTextDefaultScale(0x20, -0x28, "Hit", 6);
    configureViewport(0, 0xA0, 4.0f/3.0f);
}
"""
HITS_DIFF = """@@ -1,6 +1,6 @@
-lui    a2,%hi(D_800E1368)
+lui    a2,%hi(.rodata)
 jal    drawMenuAsciiTextDefaultScale
-lui    a2,%hi(D_800E136C)
+lui    a2,%hi(.rodata+4)
 jal    drawMenuAsciiTextDefaultScale
-lwc1    $f4,%lo(D_800E10C4)(at)
+lwc1    $f4,%lo(.rodata+8)(at)
"""

PORTRAIT = """void updateRaceSplitscreenSelectPortrait(void *arg0) {
    s16 var_v0;
    ((s16 *)&gRaceSplitscreenSelectCursorTarget + 2)[0] = var_v0;
}
"""
PORTRAIT_DIFF = """@@ -4,2 +4,2 @@
-lui    at,%hi(gRaceSplitscreenSelectPortraitAlpha)
-sh    v0,%lo(gRaceSplitscreenSelectPortraitAlpha)(at)
+lui    at,%hi(gRaceSplitscreenSelectCursorTarget)
+sh    v0,%lo(gRaceSplitscreenSelectCursorTarget+4)(at)
"""


def only(generator, source, function, diff):
    rows = list(generator(source, function, diff))
    assert len(rows) == 1
    return rows[0][2]


def test_string_literal_becomes_the_named_format_symbol():
    fixed = only(rn.literal_names, ENTRY_FEE, "drawRaceSplitscreenSelectEntryFee", ENTRY_FEE_DIFF)
    assert "extern char gRaceSplitscreenSelectEntryFeeFormat[];\nvoid drawRaceSplitscreenSelectEntryFee(" in fixed
    assert 'sprintf(sp40, gRaceSplitscreenSelectEntryFeeFormat, gRacePlayers->money);' in fixed


def test_repeated_strings_and_float_constants_map_in_rodata_order():
    fixed = only(rn.literal_names, HITS, "func_8005AE1C", HITS_DIFF)
    assert 'drawMenuAsciiTextDefaultScale(0x20, -0x48, D_800E1368, 6);' in fixed
    assert 'drawMenuAsciiTextDefaultScale(0x20, -0x28, D_800E136C, 6);' in fixed
    assert "configureViewport(0, 0xA0, D_800E10C4);" in fixed and "extern f32 D_800E10C4;" in fixed


def test_offset_expression_becomes_the_named_symbol():
    fixed = only(rn.offset_names, PORTRAIT, "updateRaceSplitscreenSelectPortrait", PORTRAIT_DIFF)
    assert "extern s16 gRaceSplitscreenSelectPortraitAlpha;\n" in fixed
    assert "    gRaceSplitscreenSelectPortraitAlpha = var_v0;" in fixed


def test_declines_when_literals_and_symbols_do_not_align():
    assert list(rn.literal_names(HITS.replace('"Hit", 6);\n    configure', '"Hit", 6);\n    x("extra");\n    configure'),
                                 "func_8005AE1C", HITS_DIFF)) == []


# 2026-09-30 residual: updateRaceSetupPlayerCountPrompt (99.83). The candidate reaches the alpha word through a struct
# field of gRaceSetupMenuSubState; the target names a separate global at that address (`+2`).
FIELD_SOURCE = """extern RaceSetupMenuSubState gRaceSetupMenuSubState;
void updateRaceSetupPlayerCountPrompt(Actor *actor) {
    gRaceSetupMenuSubState.state = actor->state;
    gRaceSetupMenuSubState.alpha = actor->alpha;
    actor->x = gRaceSetupMenuSubState.alpha;
}
"""
FIELD_DIFF = """@@ -1,6 +1,6 @@
-lui    at,%hi(gRaceSetupPlayerCountPromptAlpha)
-sh    t7,%lo(gRaceSetupPlayerCountPromptAlpha)(at)
+lui    at,%hi(gRaceSetupMenuSubState)
+sh    t7,%lo(gRaceSetupMenuSubState+2)(at)
"""


def test_field_names_fires_on_the_struct_field_residual():
    got = list(rn.field_names(FIELD_SOURCE, "updateRaceSetupPlayerCountPrompt", FIELD_DIFF))
    # one proposal per accessed field; the oracle picks the field that sits at +2
    labels = [label for label, _k, _v in got]
    assert labels == [
        "field_names:gRaceSetupMenuSubState.state[*]->gRaceSetupPlayerCountPromptAlpha",
        "field_names:gRaceSetupMenuSubState.alpha[*]->gRaceSetupPlayerCountPromptAlpha",
        "field_names:gRaceSetupMenuSubState.alpha[0]->gRaceSetupPlayerCountPromptAlpha",
        "field_names:gRaceSetupMenuSubState.alpha[1]->gRaceSetupPlayerCountPromptAlpha"]
    single = got[2][2]                                                      # only the first `.alpha` use
    assert "gRaceSetupPlayerCountPromptAlpha = actor->alpha;" in single and "actor->x = gRaceSetupMenuSubState.alpha;" in single
    alpha = got[1][2]
    assert "extern u16 gRaceSetupPlayerCountPromptAlpha;" in alpha          # width from the target's `sh`
    assert alpha.count("gRaceSetupPlayerCountPromptAlpha = actor->alpha;") == 1
    assert "actor->x = gRaceSetupPlayerCountPromptAlpha;" in alpha          # every use of the field
    assert "gRaceSetupMenuSubState.state = actor->state;" in alpha          # other fields untouched
    assert {kind for _l, kind, _v in got} == {"relocation_name"}
    assert list(rn.variants(FIELD_SOURCE, "updateRaceSetupPlayerCountPrompt", FIELD_DIFF))   # reachable via variants()


def test_field_names_declines_when_nothing_matches():
    fn = "updateRaceSetupPlayerCountPrompt"
    no_offset = FIELD_DIFF.replace("gRaceSetupMenuSubState+2", "gRaceSetupMenuSubState")
    assert not list(rn.field_names(FIELD_SOURCE, fn, no_offset))                      # same address: not an offset case
    assert not list(rn.field_names(FIELD_SOURCE.replace("gRaceSetupMenuSubState", "gOther"), fn, FIELD_DIFF))
    assert not list(rn.field_names(FIELD_SOURCE, fn, FIELD_DIFF.replace("sh ", "jal ")))   # no known access width
    assert not list(rn.field_names(FIELD_SOURCE, fn, FIELD_DIFF.replace("%hi(gRaceSetupPlayerCountPromptAlpha)", "%hi(.rodata)")
                                   .replace("%lo(gRaceSetupPlayerCountPromptAlpha)", "%lo(.rodata)")))

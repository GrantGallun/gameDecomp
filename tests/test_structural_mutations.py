"""structural_mutations fires on its 2026-09-14 census residuals and declines where the shape is absent."""
from solver import structural_mutations as sm

EXIT_MESSAGE = """void updateControllerPakRaceRecordSaveExitMessage(ControllerPakRaceRecordSaveActor *arg0) {
    u8 var_v1;

    var_v1 = (*(u8 *)((u8 *)(arg0) + 0x1E));
    switch (var_v1) {
    case 1:
        if (gMenuSelectionConfirmTimer == 0x14) {
            (*(u8 *)((u8 *)(arg0) + 0x1E)) = 2U;
            var_v1 = 2 & 0xFF;
        }
        break;
    }
}
"""

SLIDE = """void updateEndingJamSlideRightToMarker(EndingCreditsJam *arg0) {
    s32 var_a1;

    var_a1 = arg0->unk18 + 0x2E000;
    arg0->unk18 = var_a1;
    if (var_a1 < 0xFFD00000) {
        arg0->unk18 = -0x300000;
    }
}
"""

BLACK = """void osViBlack(u8 arg0) {
    volatile u8 framePad[0x8];

    u32 temp_s0;
    s32 var_v0;

    temp_s0 = __osDisableInt();
    __osRestoreInt(temp_s0);
}
"""


def rows(generator, source, function):
    return {label: variant for label, _kind, variant in generator(source, function)}


def test_chained_assignment_replaces_the_masked_constant_reload():
    fixed = rows(sm.chained_assignments, EXIT_MESSAGE, "updateControllerPakRaceRecordSaveExitMessage")["chained_assign:masked"]
    assert "            var_v1 = (*(u8 *)((u8 *)(arg0) + 0x1E)) = 2U;\n        }\n" in fixed
    assert "2 & 0xFF" not in fixed


def test_chained_assignment_declines_different_constants():
    assert rows(sm.chained_assignments, EXIT_MESSAGE.replace("2 & 0xFF", "3 & 0xFF"),
                "updateControllerPakRaceRecordSaveExitMessage") == {}


def test_high_hex_comparison_becomes_signed_negative_literal():
    fixed = rows(sm.signed_hex_compares, SLIDE, "updateEndingJamSlideRightToMarker")["signed_hex_compare:all"]
    assert "    if (var_a1 < -0x300000) {\n" in fixed
    assert rows(sm.signed_hex_compares, SLIDE.replace("0xFFD00000", "0x7FD00000"), "updateEndingJamSlideRightToMarker") == {}


def test_register_is_added_only_to_locals_named_after_callee_saved_registers():
    fixed = rows(sm.register_locals, BLACK, "osViBlack")["register_locals:saved_named"]
    assert "    register u32 temp_s0;\n    s32 var_v0;\n" in fixed
    assert "volatile u8 framePad[0x8];" in fixed
    assert rows(sm.register_locals, BLACK.replace("temp_s0", "temp_v1"), "osViBlack") == {}


def test_scheduling_signals_match_where_a_family_fires():
    for source, function in ((EXIT_MESSAGE, "updateControllerPakRaceRecordSaveExitMessage"),
                             (SLIDE, "updateEndingJamSlideRightToMarker"), (BLACK, "osViBlack")):
        assert sm.signals(source) and list(sm.variants(source, function))
    assert not sm.signals("void f(void) {\n    s32 temp_v0;\n    x = 1;\n    y = 2;\n}\n")

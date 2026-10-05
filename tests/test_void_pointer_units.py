"""void_pointer_units fires on the 2026-09-14 second-blocker shapes (after placeholder resolution) and declines elsewhere."""
from solver import void_pointer_units as vpu

# func_80058360 after placeholder resolution: a `void *` display-list cursor and a forward-declared actor.
DISPLAY_LIST = """extern void *gRegionAllocPtr;

void func_80058360(struct RaceUiAlpha18Actor *arg0) {
    void *temp_v0;

    if (arg0->unk18 != 0xFF) {
        temp_v0 = gRegionAllocPtr;
        gRegionAllocPtr = temp_v0 + 8;
        temp_v0->unk4 = 0;
        temp_v0->unk0 = 0xE7000000;
    }
}
/* Warning: struct RaceUiAlpha18Actor is not defined (only forward-declared) */
"""
DISPLAY_LIST_ASM = """glabel func_80058360
    lbu     $t6, 0x18($a0)
    sw      $zero, 4($v0)
    sw      $t7, 0($v0)
    jr      $ra
"""

CURSORS = """void renderRaceItemTextureEffects(void) {
    void **var_v1;
    s16 *var_v0;
    void *var_s0;

    var_v1 = &gRaceItemTextureEffectDrawLists;
    var_v1 += 4;
    if (!(var_v1 != &D_801121E0)) break;
    var_s0 += 0x20;
    if (var_v0 != &sp266) {
        var_s0 = var_s0 - var_s0;
    }
    temp_a0 = (bitwise u16) arg0;
}
"""


def test_fires_on_void_cursor_arithmetic_and_pseudo_fields_with_target_widths():
    lowered, report = vpu.propose(DISPLAY_LIST, "func_80058360", DISPLAY_LIST_ASM)
    assert "gRegionAllocPtr = ((u8 *)temp_v0) + 8;" in lowered
    assert "(*(s32 *)((u8 *)temp_v0 + 0x4)) = 0;" in lowered
    # Forward-declared struct pointer: the target's unanimous unsigned byte load at 0x18.
    assert "if ((*(u8 *)((u8 *)arg0 + 0x18)) != 0xFF)" in lowered
    assert {f["evidence"] for f in report["fields"]} == {"target displacement width"}


def test_fires_on_compound_updates_comparisons_and_bitwise_casts_but_keeps_pointer_differences():
    lowered, report = vpu.propose(CURSORS, "renderRaceItemTextureEffects")
    assert "var_s0 = (void *)((u8 *)var_s0 + (0x20));" in lowered
    assert "var_v1 != (void **)&D_801121E0" in lowered and "var_v0 != (s16 *)&sp266" in lowered
    assert "temp_a0 = (u16) arg0;" in lowered
    assert "var_s0 = var_s0 - var_s0;" in lowered            # a difference of two cursors keeps its meaning
    assert "var_v1 += 4;" in lowered                         # typed pointers keep C element units
    assert report["compound"] == ["var_s0"]


def test_default_word_when_target_widths_disagree_or_are_absent():
    asm = "glabel f\n    lh $t0, 4($a0)\n    lw $t1, 4($a1)\n"
    source = "void f(void *arg0) {\n    x = arg0->unk4;\n}\n"
    lowered, report = vpu.propose(source, "f", asm)
    assert "(*(s32 *)((u8 *)arg0 + 0x4))" in lowered and report["fields"][0]["evidence"] == "default word"


def test_declines_complete_types_and_ordinary_code():
    typed = "void f(Actor *arg0) {\n    arg0->unk4 = 1;\n    g(arg0 + 1);\n}\n"
    assert vpu.propose(typed, "f")[0] == typed
    assert not vpu.signals(typed, "f")
    assert vpu.signals(DISPLAY_LIST, "func_80058360")
    assert vpu.source_signals(DISPLAY_LIST) and vpu.source_signals(CURSORS)
    assert not vpu.source_signals(typed)
    assert not vpu.source_signals("void f(void) {\n    void *p;\n    g(p);\n}\n")


def test_fires_on_calls_through_loaded_words():
    # alEnvmixerParam / alRaw16Pull shape: IDO "Non-function name referenced in function call".
    source = ("void alEnvmixerParam(void *arg0, s32 filter) {\n"
              "    (*(s32 *)((unsigned char *)temp_a0 + 0x8))(temp_a0, (void *)4, filter);\n"
              "    x = (*(s32 *)((unsigned char *)arg0 + 0x30))(y);\n"
              "    z = (*(s32 *)((unsigned char *)arg0 + 0x44));\n}\n")
    lowered, calls = vpu.word_calls(source, "alEnvmixerParam")
    assert "(*(s32 (**)())((unsigned char *)temp_a0 + 0x8))(temp_a0" in lowered
    assert "x = (*(s32 (**)())((unsigned char *)arg0 + 0x30))(y);" in lowered
    assert "z = (*(s32 *)((unsigned char *)arg0 + 0x44));" in lowered      # a plain load is not a call
    assert len(calls) == 2 and vpu.source_signals(source)


def test_lowered_candidates_resolve_placeholders_first():
    source = "? g(?);  /* extern */\n" + DISPLAY_LIST
    rows = dict(vpu.lowered_candidates(source, "func_80058360", "", DISPLAY_LIST_ASM))
    combined = rows["placeholders:typed+void-units"]
    assert "s32 g(s32);" in combined and "((u8 *)temp_v0) + 8" in combined
    assert list(rows)[0] == "placeholders:typed+void-units"
